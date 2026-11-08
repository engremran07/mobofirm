"""End-user catalogue requests (brand, model, variant, firmware) and their review.

Signed-in users with a verified email submit requests; staff holding the matching
model permission review them. Approving a brand, model or variant request creates
the catalogue record and closes the request in one transaction, then notifies the
requester. Every submission is checked for an existing (or already pending) match
first, and the volume per user is rate limited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q, QuerySet
from django.db.models import Model as DjangoModel
from django.utils import timezone
from django.utils.text import slugify

from .catalog import get_firmware_model
from .constants import RESERVED_BRAND_SLUGS
from .models import (
    Brand,
    BrandCreationRequest,
    Chipset,
    ChipsetCreationRequest,
    Model,
    ModelCreationRequest,
    Variant,
    VariantCreationRequest,
)
from .notifications import notify_users
from .tracking_models import (
    FIRMWARE_REQUEST_TYPES,
    FIRMWARE_REQUEST_URGENCIES,
    FirmwareRequest,
    FirmwareRequestSupport,
)

logger = logging.getLogger(__name__)

RequestKind = Literal["brand", "model", "variant", "chipset"]
NAME_MIN = 2
NAME_MAX = 128
DETAILS_MAX = 1000
SUGGESTION_LIMIT = 6
MAX_ALIASES = 10
FIRMWARE_TYPE_KEYS = {key for key, _label in FIRMWARE_REQUEST_TYPES}
CatalogRequest = (
    BrandCreationRequest
    | ModelCreationRequest
    | VariantCreationRequest
    | ChipsetCreationRequest
)
REQUEST_MODELS: tuple[type[CatalogRequest], ...] = (
    BrandCreationRequest,
    ModelCreationRequest,
    VariantCreationRequest,
    ChipsetCreationRequest,
)


class CatalogRequestError(ValueError):
    """A request was refused; the message is safe to show to the user.

    ``existing`` is the catalogue record (or pending request) that made the
    submission redundant, when there is one.
    """

    def __init__(self, message: str, *, existing: object | None = None) -> None:
        super().__init__(message)
        self.existing = existing


class RequestRateLimited(CatalogRequestError):
    """The user submitted too many requests in the last hour."""


def requests_per_hour() -> int:
    return int(getattr(settings, "FIRMWARE_REQUESTS_PER_HOUR", 10))


def has_verified_email(user: Any) -> bool:
    from apps.users.services import is_email_verified

    return bool(getattr(user, "is_authenticated", False) and is_email_verified(user))


def ensure_can_request(user: Any) -> None:
    """Only active, signed-in users with a verified email may submit requests."""
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_active
        and has_verified_email(user)
    ):
        raise PermissionDenied("Sign in with a verified email to submit requests.")


def _check_rate(user: Any) -> None:
    since = timezone.now() - timedelta(hours=1)
    recent = sum(
        model_class.objects.filter(requested_by=user, created_at__gte=since).count()
        for model_class in (*REQUEST_MODELS, FirmwareRequest)
    )
    if recent >= requests_per_hour():
        raise RequestRateLimited(
            f"You can send at most {requests_per_hour()} requests per hour. "
            "Please try again later."
        )


def clean_name(value: str, label: str) -> str:
    name = " ".join((value or "").split())
    if len(name) < NAME_MIN or len(name) > NAME_MAX:
        raise CatalogRequestError(
            f"{label} must be between {NAME_MIN} and {NAME_MAX} characters."
        )
    if not slugify(name):
        raise CatalogRequestError(f"{label} must contain letters or numbers.")
    return name


def clean_details(value: str) -> str:
    details = (value or "").strip()
    if len(details) > DETAILS_MAX:
        raise CatalogRequestError(f"Details must not exceed {DETAILS_MAX} characters.")
    return details


# ---------------------------------------------------------------------------
# "Does it already exist?" lookups
# ---------------------------------------------------------------------------


@dataclass
class ExistenceCheck:
    """What the catalogue already holds for a typed name."""

    exact: object | None = None
    similar: list[Any] = field(default_factory=list)
    pending: list[Any] = field(default_factory=list)

    @property
    def blocks_request(self) -> bool:
        return self.exact is not None or bool(self.pending)


def _brand_query(name: str) -> Q:
    key = slugify(name)
    return Q(name__iexact=name) | Q(slug=key)


def find_brand(name: str) -> Brand | None:
    return Brand.objects.filter(_brand_query(name)).first()


def _model_query(name: str) -> Q:
    key = slugify(name)
    return (
        Q(name__iexact=name)
        | Q(marketing_name__iexact=name)
        | Q(slug=key)
        | Q(model_code__iexact=name)
    )


def find_model(brand: Brand, name: str) -> Model | None:
    return Model.objects.filter(_model_query(name), brand=brand).first()


def _variant_query(name: str) -> Q:
    key = slugify(name)
    return Q(name__iexact=name) | Q(slug=key) | Q(model_code__iexact=name)


def find_variant(model: Model, name: str) -> Variant | None:
    return Variant.objects.filter(_variant_query(name), model=model).first()


def find_chipset(vendor: str, name: str) -> Chipset | None:
    """The chipset with this vendor and name (or slug); any vendor when blank."""
    key = slugify(f"{vendor} {name}")
    if vendor:
        condition = (Q(vendor__iexact=vendor) & Q(name__iexact=name)) | Q(slug=key)
    else:
        condition = Q(name__iexact=name) | Q(slug=key)
    return Chipset.objects.filter(condition).first()


def check_existing(
    kind: RequestKind,
    name: str,
    *,
    brand: Brand | None = None,
    model: Model | None = None,
    vendor: str = "",
) -> ExistenceCheck:
    """Exact and similar catalogue matches plus pending requests for ``name``."""
    value = " ".join((name or "").split())
    if len(value) < 2:
        return ExistenceCheck()
    key = slugify(value)
    loose = Q(name__icontains=value) | (Q(slug__icontains=key) if key else Q())
    exact: object | None
    similar: QuerySet[Any]
    pending: QuerySet[Any]
    if kind == "brand":
        exact = find_brand(value)
        similar = Brand.objects.filter(loose).order_by("name")
        pending = BrandCreationRequest.objects.filter(
            status="pending", name__iexact=value
        )
    elif kind == "chipset":
        vendor_text = " ".join((vendor or "").split())
        exact = find_chipset(vendor_text, value)
        similar = Chipset.objects.filter(loose | Q(family__icontains=value)).order_by(
            "vendor", "name"
        )
        pending = ChipsetCreationRequest.objects.filter(
            status="pending", name__iexact=value
        )
    elif kind == "model":
        if brand is None:
            return ExistenceCheck()
        exact = find_model(brand, value)
        similar = Model.objects.filter(
            loose | Q(marketing_name__icontains=value) | Q(model_code__icontains=value),
            brand=brand,
        ).order_by("name")
        pending = ModelCreationRequest.objects.filter(
            status="pending", brand=brand, name__iexact=value
        )
    else:
        if model is None:
            return ExistenceCheck()
        exact = find_variant(model, value)
        similar = Variant.objects.filter(
            loose | Q(model_code__icontains=value), model=model
        ).order_by("name")
        pending = VariantCreationRequest.objects.filter(
            status="pending", model=model, name__iexact=value
        )
    return ExistenceCheck(
        exact=exact,
        similar=list(similar[:SUGGESTION_LIMIT]),
        pending=list(pending[:SUGGESTION_LIMIT]),
    )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _refuse_existing(check: ExistenceCheck, label: str) -> None:
    if check.exact is not None:
        raise CatalogRequestError(
            f"{label} already exists in the catalogue.", existing=check.exact
        )
    if check.pending:
        raise CatalogRequestError(
            f"A request for this {label.lower()} is already waiting for review.",
            existing=check.pending[0],
        )


def submit_brand_request(user: Any, *, name: str, details: str = "") -> Any:
    ensure_can_request(user)
    value = clean_name(name, "Brand name")
    if slugify(value) in RESERVED_BRAND_SLUGS:
        raise CatalogRequestError("This name is reserved and cannot be a brand.")
    text = clean_details(details)
    _check_rate(user)
    _refuse_existing(check_existing("brand", value), "Brand")
    try:
        with transaction.atomic():
            return BrandCreationRequest.objects.create(
                name=value, details=text, requested_by=user
            )
    except IntegrityError as error:
        raise CatalogRequestError(
            "A request for this brand is already waiting for review."
        ) from error


def submit_model_request(
    user: Any,
    *,
    brand: Brand,
    name: str,
    model_code: str = "",
    chipset: Chipset | None = None,
    details: str = "",
) -> Any:
    """Request a model: the brand is required, the chipset is optional."""
    ensure_can_request(user)
    value = clean_name(name, "Model name")
    text = clean_details(details)
    code = " ".join((model_code or "").split())[:64]
    _check_rate(user)
    _refuse_existing(check_existing("model", value, brand=brand), "Model")
    try:
        with transaction.atomic():
            return ModelCreationRequest.objects.create(
                brand=brand,
                name=value,
                model_code=code,
                chipset=chipset,
                details=text,
                requested_by=user,
            )
    except IntegrityError as error:
        raise CatalogRequestError(
            "A request for this model is already waiting for review."
        ) from error


def submit_variant_request(
    user: Any,
    *,
    model: Model,
    name: str,
    model_code: str = "",
    region: str = "",
    details: str = "",
) -> Any:
    ensure_can_request(user)
    value = clean_name(name, "Variant name")
    text = clean_details(details)
    code = " ".join((model_code or "").split())[:64]
    area = " ".join((region or "").split())[:64]
    _check_rate(user)
    _refuse_existing(check_existing("variant", value, model=model), "Variant")
    try:
        with transaction.atomic():
            return VariantCreationRequest.objects.create(
                model=model,
                name=value,
                model_code=code,
                region=area,
                details=text,
                requested_by=user,
            )
    except IntegrityError as error:
        raise CatalogRequestError(
            "A request for this variant is already waiting for review."
        ) from error


def submit_chipset_request(
    user: Any,
    *,
    vendor: str,
    name: str,
    family: str = "",
    aliases: str | list[str] = "",
    details: str = "",
) -> Any:
    ensure_can_request(user)
    vendor_name = clean_name(vendor, "Vendor")[:64]
    value = clean_name(name, "Chipset name")
    family_name = " ".join((family or "").split())[:64]
    text = clean_details(details)
    raw_aliases = aliases.split(",") if isinstance(aliases, str) else aliases
    alias_list = list(
        dict.fromkeys(
            alias
            for alias in (" ".join(str(item).split())[:64] for item in raw_aliases)
            if alias
        )
    )
    if len(alias_list) > MAX_ALIASES:
        raise CatalogRequestError(f"Give at most {MAX_ALIASES} alternative names.")
    _check_rate(user)
    check = check_existing("chipset", value, vendor=vendor_name)
    _refuse_existing(check, "Chipset")
    try:
        with transaction.atomic():
            return ChipsetCreationRequest.objects.create(
                vendor=vendor_name,
                name=value,
                family=family_name,
                aliases=alias_list,
                details=text,
                requested_by=user,
            )
    except IntegrityError as error:
        raise CatalogRequestError(
            "A request for this chipset is already waiting for review."
        ) from error


def submit_firmware_request(
    user: Any,
    *,
    brand: Brand,
    model: Model | None = None,
    variant_name: str = "",
    firmware_type: str = "official",
    urgency: int = 2,
    description: str = "",
) -> tuple[FirmwareRequest, bool]:
    """Ask for a missing firmware; the same request from another user adds demand.

    Returns ``(request, created)``. A user supporting a request they already
    support is a no-op, so demand counts each person once.
    """
    ensure_can_request(user)
    if model is not None and model.brand_id != brand.pk:
        raise CatalogRequestError("The selected model does not belong to the brand.")
    if firmware_type not in FIRMWARE_TYPE_KEYS:
        raise CatalogRequestError("Choose a valid firmware type.")
    if urgency not in {choice for choice, _label in _urgency_choices()}:
        raise CatalogRequestError("Choose a valid urgency.")
    variant = " ".join((variant_name or "").split())[:200]
    text = clean_details(description)
    _refuse_existing_firmware(model, firmware_type)

    key = {
        "brand": brand,
        "model": model,
        "variant_name__iexact": variant,
        "firmware_type": firmware_type,
    }
    existing = FirmwareRequest.objects.filter(**key).first()
    if existing is not None:
        return _support_request(user, existing), False
    _check_rate(user)
    try:
        with transaction.atomic():
            created = FirmwareRequest.objects.create(
                brand=brand,
                model=model,
                variant_name=variant,
                firmware_type=firmware_type,
                urgency=urgency,
                description=text,
                requested_by=user,
            )
            FirmwareRequestSupport.objects.create(request=created, user=user)
    except IntegrityError:
        concurrent = FirmwareRequest.objects.filter(**key).first()
        if concurrent is None:
            raise
        return _support_request(user, concurrent), False
    return created, True


def _urgency_choices() -> list[tuple[int, str]]:
    return [(value, label) for value, label in FIRMWARE_REQUEST_URGENCIES]


def _refuse_existing_firmware(model: Model | None, firmware_type: str) -> None:
    if model is None:
        return
    model_class = get_firmware_model(firmware_type)
    if model_class is None:
        return
    if model_class.objects.filter(
        model=model, is_active=True, is_verified=True
    ).exists():
        raise CatalogRequestError(
            "Firmware of this type is already published for this model.",
            existing=model,
        )


def _support_request(user: Any, request: FirmwareRequest) -> FirmwareRequest:
    """Add ``user`` to the demand of ``request`` once; refuse closed requests."""
    if request.status == "fulfilled":
        raise CatalogRequestError(
            "This firmware has already been added. Check the model page.",
            existing=request,
        )
    if request.status == "rejected":
        reason = f" Reason: {request.admin_notes}" if request.admin_notes else ""
        raise CatalogRequestError(
            f"This request was declined by the reviewers.{reason}", existing=request
        )
    with transaction.atomic():
        _support, created = FirmwareRequestSupport.objects.get_or_create(
            request=request, user=user
        )
        if created:
            FirmwareRequest.objects.filter(pk=request.pk).update(
                request_count=F("request_count") + 1,
                last_requested_at=timezone.now(),
            )
    request.refresh_from_db()
    return request


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _require_reviewer(user: Any, model_class: type[DjangoModel]) -> None:
    opts = model_class._meta
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_staff
        and user.has_perm(f"{opts.app_label}.change_{opts.model_name}")
    ):
        raise PermissionDenied("Review permission for this request type is required.")


def _clean_reason(reason: str, *, required: bool) -> str:
    value = (reason or "").strip()
    if required and not value:
        raise ValidationError("A reason is required to reject a request.")
    if len(value) > 1000:
        raise ValidationError("The reason must not exceed 1000 characters.")
    return value


def _free_slug(queryset: QuerySet[Any], base: str) -> str:
    """``base`` or the first ``base-N`` not used in ``queryset``."""
    slug, counter = base, 2
    while queryset.filter(slug=slug).exists():
        slug = f"{base}-{counter}"[:140]
        counter += 1
    return slug


def _decide(
    pending: Any, reviewer: Any, status: str, reason: str, subject: str
) -> None:
    pending.status = status
    pending.reviewed_by = reviewer
    pending.reviewed_at = timezone.now()
    pending.notes = reason
    pending.save(update_fields=["status", "reviewed_by", "reviewed_at", "notes"])
    requester_id = pending.requested_by_id
    sender = type(pending)

    def send() -> None:
        if requester_id is None:
            return
        verdict = "approved" if status == "approved" else "declined"
        suffix = f" Reason: {reason}" if reason else ""
        notify_users(
            [requester_id],
            sender=sender,
            title=f"Request {verdict}",
            message=f"Your request for {subject} was {verdict}.{suffix}",
            priority="info" if status == "approved" else "warning",
            subject=pending.pk,
        )

    transaction.on_commit(send)


def _lock_pending(model_class: Any, pk: int) -> Any:
    locked = model_class.objects.select_for_update().get(pk=pk)
    if locked.status != "pending":
        raise ValidationError("Only pending requests can be decided.")
    return locked


def approve_brand_request(
    request: BrandCreationRequest, reviewer: Any, *, reason: str = ""
) -> tuple[Brand, bool]:
    """Create (or link) the brand and approve the request atomically."""
    _require_reviewer(reviewer, BrandCreationRequest)
    note = _clean_reason(reason, required=False)
    with transaction.atomic():
        locked = _lock_pending(BrandCreationRequest, request.pk)
        slug = slugify(locked.name)
        if not slug or slug in RESERVED_BRAND_SLUGS:
            raise ValidationError(
                f"“{locked.name}” is reserved and cannot be created as a brand."
            )
        brand = Brand.objects.filter(Q(name__iexact=locked.name) | Q(slug=slug)).first()
        created = brand is None
        if brand is None:
            brand = Brand.objects.create(
                name=locked.name, slug=_free_slug(Brand.objects.all(), slug)
            )
        _decide(locked, reviewer, "approved", note, f"the brand “{brand.name}”")
    logger.info("Brand request %s approved as brand %s", request.pk, brand.pk)
    request.refresh_from_db()
    return brand, created


def approve_model_request(
    request: ModelCreationRequest, reviewer: Any, *, reason: str = ""
) -> tuple[Model, bool]:
    _require_reviewer(reviewer, ModelCreationRequest)
    note = _clean_reason(reason, required=False)
    with transaction.atomic():
        locked = _lock_pending(ModelCreationRequest, request.pk)
        model = find_model(locked.brand, locked.name)
        created = model is None
        if model is None:
            siblings = Model.objects.filter(brand=locked.brand)
            model = Model.objects.create(
                brand=locked.brand,
                name=locked.name,
                slug=_free_slug(siblings, slugify(locked.name)[:160]),
                model_code=locked.model_code,
                chipset_ref=locked.chipset,
            )
        _decide(
            locked,
            reviewer,
            "approved",
            note,
            f"the model “{model.name}” ({locked.brand.name})",
        )
    logger.info("Model request %s approved as model %s", request.pk, model.pk)
    request.refresh_from_db()
    return model, created


def approve_variant_request(
    request: VariantCreationRequest, reviewer: Any, *, reason: str = ""
) -> tuple[Variant, bool]:
    _require_reviewer(reviewer, VariantCreationRequest)
    note = _clean_reason(reason, required=False)
    with transaction.atomic():
        locked = _lock_pending(VariantCreationRequest, request.pk)
        variant = find_variant(locked.model, locked.name)
        created = variant is None
        if variant is None:
            siblings = Variant.objects.filter(model=locked.model)
            variant = Variant.objects.create(
                model=locked.model,
                name=locked.name,
                slug=_free_slug(siblings, slugify(locked.name)[:160]),
                model_code=locked.model_code,
                region=locked.region,
            )
        _decide(
            locked,
            reviewer,
            "approved",
            note,
            f"the variant “{variant.name}” of {locked.model.display_name}",
        )
    logger.info("Variant request %s approved as variant %s", request.pk, variant.pk)
    request.refresh_from_db()
    return variant, created


def approve_chipset_request(
    request: ChipsetCreationRequest, reviewer: Any, *, reason: str = ""
) -> tuple[Chipset, bool]:
    """Create (or link) the chipset and approve the request atomically."""
    _require_reviewer(reviewer, ChipsetCreationRequest)
    note = _clean_reason(reason, required=False)
    with transaction.atomic():
        locked = _lock_pending(ChipsetCreationRequest, request.pk)
        chipset = find_chipset(locked.vendor, locked.name)
        created = chipset is None
        if chipset is None:
            chipset = Chipset.objects.create(
                vendor=locked.vendor,
                name=locked.name,
                family=locked.family,
                aliases=list(locked.aliases),
                slug=_free_slug(
                    Chipset.objects.all(),
                    slugify(f"{locked.vendor} {locked.name}")[:160],
                ),
            )
        _decide(
            locked,
            reviewer,
            "approved",
            note,
            f"the chipset “{chipset.display_name}”",
        )
    logger.info("Chipset request %s approved as chipset %s", request.pk, chipset.pk)
    request.refresh_from_db()
    return chipset, created


def reject_catalog_request(request: Any, reviewer: Any, *, reason: str) -> None:
    """Reject a pending catalogue request with a recorded reason."""
    model_class = type(request)
    if model_class not in REQUEST_MODELS:
        raise ValidationError("Unsupported request type.")
    _require_reviewer(reviewer, model_class)
    note = _clean_reason(reason, required=True)
    with transaction.atomic():
        locked = _lock_pending(model_class, request.pk)
        label = (
            f"{locked.vendor} {locked.name}"
            if isinstance(locked, ChipsetCreationRequest)
            else locked.name
        )
        _decide(locked, reviewer, "rejected", note, f"“{label}”")
    request.refresh_from_db()


def decide_firmware_request(
    request: FirmwareRequest, reviewer: Any, *, status: str, reason: str = ""
) -> FirmwareRequest:
    """Move a firmware request through open → in progress → fulfilled/rejected."""
    _require_reviewer(reviewer, FirmwareRequest)
    if status not in {"open", "in_progress", "fulfilled", "rejected"}:
        raise ValidationError("Unsupported firmware request status.")
    note = _clean_reason(reason, required=status == "rejected")
    with transaction.atomic():
        locked = FirmwareRequest.objects.select_for_update().get(pk=request.pk)
        if locked.status in {"fulfilled", "rejected"} and status != locked.status:
            raise ValidationError("This request was already closed.")
        locked.status = status
        locked.reviewed_by = reviewer
        locked.reviewed_at = timezone.now()
        locked.admin_notes = note
        if status == "fulfilled":
            locked.fulfilled_at = locked.reviewed_at
        locked.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "admin_notes",
                "fulfilled_at",
            ]
        )
        supporters = list(locked.supporters.values_list("user_id", flat=True))
        target = _firmware_request_label(locked)

        def send() -> None:
            if status not in {"fulfilled", "rejected"}:
                return
            verdict = "fulfilled" if status == "fulfilled" else "declined"
            suffix = f" Note: {note}" if note else ""
            notify_users(
                supporters,
                sender=FirmwareRequest,
                title=f"Firmware request {verdict}",
                message=f"Your request for {target} was {verdict}.{suffix}",
                priority="info" if status == "fulfilled" else "warning",
                subject=locked.pk,
            )

        transaction.on_commit(send)
    request.refresh_from_db()
    return request


def fulfil_matching_requests(firmware: Any, category: str, reviewer: Any) -> list[int]:
    """Close the open firmware requests a newly approved file satisfies.

    A request matches on model and firmware type; one naming a variant only
    matches that variant. Runs inside the approval transaction and returns the
    fulfilled request ids; supporters are notified after commit.
    """
    if firmware.model_id is None:
        return []
    variant_name = firmware.variant.name if firmware.variant_id else ""
    matches = FirmwareRequest.objects.select_for_update().filter(
        Q(variant_name="") | Q(variant_name__iexact=variant_name),
        model_id=firmware.model_id,
        firmware_type=category,
        status__in=("open", "in_progress"),
    )
    content_type = ContentType.objects.get_for_model(firmware, for_concrete_model=False)
    now = timezone.now()
    fulfilled: list[int] = []
    for match in matches:
        match.status = "fulfilled"
        match.fulfilled_firmware_ct = content_type
        match.fulfilled_firmware_id = firmware.pk
        match.fulfilled_at = now
        match.reviewed_by = reviewer
        match.reviewed_at = now
        match.save(
            update_fields=[
                "status",
                "fulfilled_firmware_ct",
                "fulfilled_firmware_id",
                "fulfilled_at",
                "reviewed_by",
                "reviewed_at",
            ]
        )
        fulfilled.append(match.pk)
        _notify_fulfilled(match)
    return fulfilled


def _notify_fulfilled(match: FirmwareRequest) -> None:
    supporters = list(match.supporters.values_list("user_id", flat=True))
    target = _firmware_request_label(match)

    def send() -> None:
        notify_users(
            supporters,
            sender=FirmwareRequest,
            title="Firmware request fulfilled",
            message=f"Your request for {target} was fulfilled: the firmware is published.",
            priority="info",
            subject=match.pk,
        )

    transaction.on_commit(send)


def _firmware_request_label(request: FirmwareRequest) -> str:
    parts = [request.brand.name]
    if request.model is not None:
        parts.append(request.model.display_name)
    if request.variant_name:
        parts.append(request.variant_name)
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# "My requests"
# ---------------------------------------------------------------------------


@dataclass
class MyRequests:
    brands: list[BrandCreationRequest]
    models: list[ModelCreationRequest]
    variants: list[VariantCreationRequest]
    chipsets: list[ChipsetCreationRequest]
    firmware: list[FirmwareRequest]

    @property
    def total(self) -> int:
        return (
            len(self.brands)
            + len(self.models)
            + len(self.variants)
            + len(self.chipsets)
            + len(self.firmware)
        )


def my_requests(user: Any) -> MyRequests:
    return MyRequests(
        chipsets=list(
            ChipsetCreationRequest.objects.filter(requested_by=user).select_related(
                "reviewed_by"
            )
        ),
        brands=list(
            BrandCreationRequest.objects.filter(requested_by=user).select_related(
                "reviewed_by"
            )
        ),
        models=list(
            ModelCreationRequest.objects.filter(requested_by=user).select_related(
                "brand", "reviewed_by"
            )
        ),
        variants=list(
            VariantCreationRequest.objects.filter(requested_by=user).select_related(
                "model__brand", "reviewed_by"
            )
        ),
        firmware=list(
            FirmwareRequest.objects.filter(supporters__user=user)
            .select_related("brand", "model")
            .order_by("-created_at")
        ),
    )
