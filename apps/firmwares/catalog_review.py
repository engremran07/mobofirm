"""Review of catalogue requests: approve, reject and firmware request decisions.

Every decision runs in one transaction under a row lock, writes the admin history
(``LogEntry``) and notifies the requester after commit. The admin queues
(``admin_requests``), the REST API and the services all call these functions, so
permissions, idempotency and audit behave identically everywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Model as DjangoModel
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.text import slugify

from .catalog_lookup import (
    NAME_MAX,
    REQUEST_MODELS,
    CatalogRequestError,
    clean_name,
    find_variant,
    model_query,
    variant_query,
)
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
from .tracking_models import FirmwareRequest
from .variant_rules import clean_text, normalize_variant_code, parse_memory

logger = logging.getLogger(__name__)

REASON_MAX = 1000


@dataclass(frozen=True)
class Overrides:
    """Corrections a reviewer makes before approving; ``None`` keeps the request."""

    name: str | None = None
    slug: str | None = None
    model_code: str | None = None
    region: str | None = None
    vendor: str | None = None
    variant_name: str | None = None

    @classmethod
    def from_options(cls, options: Mapping[str, str]) -> Overrides:
        """The overrides present in ``options`` (unknown keys are ignored)."""
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in options.items() if key in known})


@dataclass(frozen=True)
class Verdict:
    """What was decided about a request, for the audit entry and the notice."""

    status: str
    reason: str
    subject: str
    entity: DjangoModel | None = None
    created: bool = False


APP_LABEL = "firmwares"


def _require_reviewer(user: Any, model_class: type[DjangoModel]) -> None:
    permission = f"{APP_LABEL}.change_{model_class.__name__.lower()}"
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_staff
        and user.has_perm(permission)
    ):
        raise PermissionDenied("Review permission for this request type is required.")


def _clean_reason(reason: str, *, required: bool) -> str:
    value = (reason or "").strip()
    if required and not value:
        raise ValidationError("A reason is required to reject a request.")
    if len(value) > REASON_MAX:
        raise ValidationError(f"The reason must not exceed {REASON_MAX} characters.")
    return value


def free_slug(queryset: QuerySet[Any], base: str) -> str:
    """``base`` or the first ``base-N`` not used in ``queryset``."""
    slug, counter = base, 2
    while queryset.filter(slug=slug).exists():
        slug = f"{base}-{counter}"[:140]
        counter += 1
    return slug


def _final_name(override: str | None, current: str, label: str) -> str:
    """The reviewer's corrected name, validated like a submitted one."""
    if override is None or not override.strip():
        return current
    try:
        return clean_name(override, label)
    except CatalogRequestError as error:
        raise ValidationError(str(error)) from error


def _final_slug(override: str | None, name: str, limit: int) -> str:
    """The reviewer's slug (or one derived from ``name``), never empty."""
    slug = slugify(override or "") or slugify(name)
    if not slug:
        raise ValidationError("The name must contain letters or numbers.")
    return slug[:limit]


def _audit_decision(pending: Any, reviewer: Any, verdict: Verdict) -> None:
    """Write the decision (and a newly created record) to the admin history."""
    entries = LogEntry.objects
    if verdict.entity is not None and verdict.created:
        entries.log_actions(
            reviewer.pk,
            [verdict.entity],
            ADDITION,
            f"Created from request #{pending.pk}.",
            single_object=True,
        )
    word = "Rejected" if verdict.status == "rejected" else "Approved"
    linked = ""
    if verdict.entity is not None and not verdict.created:
        linked = " (linked to the existing record)"
    detail = f" Reason: {verdict.reason}" if verdict.reason else ""
    entries.log_actions(
        reviewer.pk,
        [pending],
        CHANGE,
        f"{word} the request for {verdict.subject}{linked}.{detail}",
        single_object=True,
    )


def _decide(pending: Any, reviewer: Any, verdict: Verdict) -> None:
    """Close ``pending`` with ``verdict``; the requester hears about it on commit."""
    pending.status = verdict.status
    pending.reviewed_by = reviewer
    pending.reviewed_at = timezone.now()
    pending.notes = verdict.reason
    pending.save(update_fields=["status", "reviewed_by", "reviewed_at", "notes"])
    _audit_decision(pending, reviewer, verdict)
    requester_id = pending.requested_by_id
    sender = type(pending)

    def send() -> None:
        if requester_id is None:
            return
        approved = verdict.status == "approved"
        word = "approved" if approved else "declined"
        suffix = f" Reason: {verdict.reason}" if verdict.reason else ""
        notify_users(
            [requester_id],
            sender=sender,
            title=f"Request {word}",
            message=f"Your request for {verdict.subject} was {word}.{suffix}",
            priority="info" if approved else "warning",
            subject=pending.pk,
        )

    transaction.on_commit(send)


def _lock_pending(model_class: Any, pk: int) -> Any:
    locked = model_class.objects.select_for_update().get(pk=pk)
    if locked.status != "pending":
        raise ValidationError("Only pending requests can be decided.")
    return locked


def approve_brand_request(
    request: BrandCreationRequest,
    reviewer: Any,
    *,
    reason: str = "",
    overrides: Overrides | None = None,
) -> tuple[Brand, bool]:
    """Create (or link) the brand and approve the request atomically.

    ``overrides`` (name, slug) let the reviewer correct the requested data first.
    """
    _require_reviewer(reviewer, BrandCreationRequest)
    note = _clean_reason(reason, required=False)
    fixes = overrides or Overrides()
    with transaction.atomic():
        locked = _lock_pending(BrandCreationRequest, request.pk)
        name = _final_name(fixes.name, locked.name, "Brand name")
        wanted = _final_slug(fixes.slug, name, 140)
        if wanted in RESERVED_BRAND_SLUGS:
            raise ValidationError(
                f"“{name}” is reserved and cannot be created as a brand."
            )
        brand = Brand.objects.filter(Q(name__iexact=name) | Q(slug=wanted)).first()
        created = brand is None
        if brand is None:
            brand = Brand.objects.create(
                name=name, slug=free_slug(Brand.objects.all(), wanted)
            )
        verdict = Verdict("approved", note, f"the brand “{brand.name}”", brand, created)
        _decide(locked, reviewer, verdict)
    logger.info("Brand request %s approved as brand %s", request.pk, brand.pk)
    request.refresh_from_db()
    return brand, created


def approve_model_request(
    request: ModelCreationRequest,
    reviewer: Any,
    *,
    reason: str = "",
    overrides: Overrides | None = None,
) -> tuple[Model, bool]:
    """Create (or link) the model under the requested brand and approve."""
    _require_reviewer(reviewer, ModelCreationRequest)
    note = _clean_reason(reason, required=False)
    fixes = overrides or Overrides()
    with transaction.atomic():
        locked = _lock_pending(ModelCreationRequest, request.pk)
        name = _final_name(fixes.name, locked.name, "Model name")
        wanted = _final_slug(fixes.slug, name, 160)
        code = locked.model_code if fixes.model_code is None else fixes.model_code
        model = Model.objects.filter(
            model_query(name) | Q(slug=wanted), brand=locked.brand
        ).first()
        created = model is None
        if model is None:
            model = Model.objects.create(
                brand=locked.brand,
                name=name,
                slug=free_slug(Model.objects.filter(brand=locked.brand), wanted),
                model_code=clean_text(code, 64),
                chipset_ref=locked.chipset,
            )
        subject = f"the model “{model.name}” ({locked.brand.name})"
        _decide(locked, reviewer, Verdict("approved", note, subject, model, created))
    logger.info("Model request %s approved as model %s", request.pk, model.pk)
    request.refresh_from_db()
    return model, created


def _create_variant(
    locked: VariantCreationRequest, name: str, slug: str, code: str, region: str
) -> Variant:
    """The variant a request describes, with RAM/storage read from its memory."""
    ram, storage = parse_memory(locked.memory)
    return Variant.objects.create(
        model=locked.model,
        name=name,
        slug=free_slug(Variant.objects.filter(model=locked.model), slug),
        model_code=code,
        region=region,
        carrier=locked.carrier,
        ram_options=ram,
        storage_options=storage,
    )


def approve_variant_request(
    request: VariantCreationRequest,
    reviewer: Any,
    *,
    reason: str = "",
    overrides: Overrides | None = None,
) -> tuple[Variant, bool]:
    """Create (or link) the variant under the requested model and approve."""
    _require_reviewer(reviewer, VariantCreationRequest)
    note = _clean_reason(reason, required=False)
    fixes = overrides or Overrides()
    with transaction.atomic():
        locked = _lock_pending(VariantCreationRequest, request.pk)
        name = _final_name(fixes.name, locked.name, "Variant name")
        code = normalize_variant_code(
            locked.model_code if fixes.model_code is None else fixes.model_code
        )
        region = clean_text(locked.region if fixes.region is None else fixes.region, 64)
        wanted = _final_slug(fixes.slug, name or code, 160)
        variant = Variant.objects.filter(
            variant_query(name, code) | Q(slug=wanted), model=locked.model
        ).first()
        created = variant is None
        if variant is None:
            variant = _create_variant(locked, name or code, wanted, code, region)
        subject = f"the variant “{variant.name}” of {locked.model.display_name}"
        _decide(locked, reviewer, Verdict("approved", note, subject, variant, created))
    logger.info("Variant request %s approved as variant %s", request.pk, variant.pk)
    request.refresh_from_db()
    return variant, created


def approve_chipset_request(
    request: ChipsetCreationRequest,
    reviewer: Any,
    *,
    reason: str = "",
    overrides: Overrides | None = None,
) -> tuple[Chipset, bool]:
    """Create (or link) the chipset and approve the request atomically."""
    _require_reviewer(reviewer, ChipsetCreationRequest)
    note = _clean_reason(reason, required=False)
    fixes = overrides or Overrides()
    with transaction.atomic():
        locked = _lock_pending(ChipsetCreationRequest, request.pk)
        vendor = _final_name(fixes.vendor, locked.vendor, "Vendor")[:64]
        name = _final_name(fixes.name, locked.name, "Chipset name")
        wanted = _final_slug(fixes.slug, f"{vendor} {name}", 160)
        chipset = Chipset.objects.filter(
            (Q(vendor__iexact=vendor) & Q(name__iexact=name)) | Q(slug=wanted)
        ).first()
        created = chipset is None
        if chipset is None:
            chipset = Chipset.objects.create(
                vendor=vendor,
                name=name,
                family=locked.family,
                aliases=list(locked.aliases),
                slug=free_slug(Chipset.objects.all(), wanted),
            )
        subject = f"the chipset “{chipset.display_name}”"
        _decide(locked, reviewer, Verdict("approved", note, subject, chipset, created))
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
        _decide(locked, reviewer, Verdict("rejected", note, f"“{label}”"))
    request.refresh_from_db()


def firmware_request_label(request: FirmwareRequest) -> str:
    """``Brand / Model / Variant`` of a firmware request, for notices."""
    parts = [request.brand.name]
    if request.model is not None:
        parts.append(request.model.display_name)
    if request.variant_name:
        parts.append(request.variant_name)
    return " / ".join(parts)


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
        _store_firmware_decision(locked, reviewer, status, note)
        _announce_firmware_decision(locked, reviewer, Verdict(status, note, ""))
    request.refresh_from_db()
    return request


def _store_firmware_decision(
    locked: FirmwareRequest, reviewer: Any, status: str, note: str
) -> None:
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


FIRMWARE_VERDICT_WORDS = {
    "fulfilled": "fulfilled",
    "rejected": "declined",
    "in_progress": "approved",
}


def _announce_firmware_decision(
    locked: FirmwareRequest, reviewer: Any, verdict: Verdict
) -> None:
    """Audit the decision and notify every supporter once it commits."""
    supporters = list(locked.supporters.values_list("user_id", flat=True))
    target = firmware_request_label(locked)
    _audit_decision(
        locked,
        reviewer,
        Verdict(
            verdict.status, verdict.reason, target, verdict.entity, verdict.created
        ),
    )

    def send() -> None:
        word = FIRMWARE_VERDICT_WORDS.get(verdict.status)
        if word is None:
            return
        suffix = f" Note: {verdict.reason}" if verdict.reason else ""
        notify_users(
            supporters,
            sender=FirmwareRequest,
            title=f"Firmware request {word}",
            message=f"Your request for {target} was {word}.{suffix}",
            priority="warning" if verdict.status == "rejected" else "info",
            subject=locked.pk,
        )

    transaction.on_commit(send)


def approve_firmware_request(
    request: FirmwareRequest,
    reviewer: Any,
    *,
    reason: str = "",
    overrides: Overrides | None = None,
) -> tuple[Variant | None, bool]:
    """Accept an open firmware request and make sure its catalogue target exists.

    The model is always known; when the request names a variant that the model
    lacks, the variant is created (or linked when it exists) so the file can be
    uploaded against it. Returns ``(variant or None, created)``; the request
    moves to *in progress* and every supporter is told it was approved.
    """
    _require_reviewer(reviewer, FirmwareRequest)
    note = _clean_reason(reason, required=False)
    fixes = overrides or Overrides()
    with transaction.atomic():
        locked = FirmwareRequest.objects.select_for_update().get(pk=request.pk)
        if locked.status != "open":
            raise ValidationError("Only open requests can be approved.")
        wanted = clean_text(
            locked.variant_name if fixes.variant_name is None else fixes.variant_name,
            200,
        )
        variant, created = _ensure_requested_variant(locked, wanted)
        _store_firmware_decision(locked, reviewer, "in_progress", note)
        verdict = Verdict("in_progress", note, "", variant, created)
        _announce_firmware_decision(locked, reviewer, verdict)
    request.refresh_from_db()
    return variant, created


def _ensure_requested_variant(
    locked: FirmwareRequest, wanted: str
) -> tuple[Variant | None, bool]:
    """Find or create the variant a firmware request names under its model."""
    if locked.model is None or not wanted:
        return None, False
    variant = find_variant(locked.model, wanted, normalize_variant_code(wanted))
    if variant is not None:
        return variant, False
    siblings = Variant.objects.filter(model=locked.model)
    variant = Variant.objects.create(
        model=locked.model,
        name=wanted[:NAME_MAX],
        slug=free_slug(siblings, slugify(wanted)[:160] or "variant"),
        model_code=normalize_variant_code(wanted) if " " not in wanted else "",
    )
    return variant, True


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
    target = firmware_request_label(match)

    def send() -> None:
        notify_users(
            supporters,
            sender=FirmwareRequest,
            title="Firmware request fulfilled",
            message=(
                f"Your request for {target} was fulfilled: the firmware is published."
            ),
            priority="info",
            subject=match.pk,
        )

    transaction.on_commit(send)
