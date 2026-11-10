"""End-user catalogue requests (brand, model, variant, chipset, firmware).

Signed-in users with a verified email submit requests; every submission is checked
for an existing (or already pending) match first and the volume per user is rate
limited. Staff decisions live in ``catalog_review`` (re-exported here for the REST
API and older callers); the shared rules live in ``catalog_lookup``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from .catalog import get_firmware_model
from .catalog_lookup import (
    FIRMWARE_TYPE_KEYS,
    MAX_ALIASES,
    NAME_MAX,
    CatalogRequestError,
    ExistenceCheck,
    RequestKind,
    RequestRateLimited,
    check_existing,
    check_rate,
    clean_details,
    clean_name,
    ensure_can_request,
    find_brand,
    find_chipset,
    find_model,
    find_variant,
    has_verified_email,
    requests_per_hour,
)
from .catalog_review import (
    Overrides,
    approve_brand_request,
    approve_chipset_request,
    approve_firmware_request,
    approve_model_request,
    approve_variant_request,
    decide_firmware_request,
    fulfil_matching_requests,
    reject_catalog_request,
)
from .constants import RESERVED_BRAND_SLUGS
from .models import (
    Brand,
    BrandCreationRequest,
    Chipset,
    ChipsetCreationRequest,
    Model,
    ModelCreationRequest,
    VariantCreationRequest,
)
from .tracking_models import (
    FIRMWARE_REQUEST_URGENCIES,
    FirmwareRequest,
    FirmwareRequestSupport,
)
from .variant_rules import VariantIdentity

__all__ = [
    "CatalogRequestError",
    "ExistenceCheck",
    "MyRequests",
    "Overrides",
    "RequestKind",
    "RequestRateLimited",
    "approve_brand_request",
    "approve_chipset_request",
    "approve_firmware_request",
    "approve_model_request",
    "approve_variant_request",
    "check_existing",
    "decide_firmware_request",
    "find_brand",
    "find_chipset",
    "find_model",
    "find_variant",
    "fulfil_matching_requests",
    "has_verified_email",
    "my_requests",
    "reject_catalog_request",
    "requests_per_hour",
    "submit_brand_request",
    "submit_chipset_request",
    "submit_firmware_request",
    "submit_model_request",
    "submit_variant_request",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _refuse_existing(check: ExistenceCheck, label: str) -> None:
    """Refuse a submission that duplicates a record or a pending request."""
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
    """Request a brand that is missing from the catalogue."""
    ensure_can_request(user)
    value = clean_name(name, "Brand name")
    if slugify(value) in RESERVED_BRAND_SLUGS:
        raise CatalogRequestError("This name is reserved and cannot be a brand.")
    text = clean_details(details)
    check_rate(user)
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
    check_rate(user)
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
    identity: VariantIdentity,
    name: str = "",
    details: str = "",
) -> Any:
    """Request a variant of ``model``.

    ``identity`` needs at least one identifying field: the variant code, the
    region/market, RAM / storage or the carrier. The name is optional; a blank
    one becomes a label built from those fields.
    """
    ensure_can_request(user)
    identity = identity.normalized()
    if not identity.is_identified:
        raise CatalogRequestError(
            "Give at least one of: the variant code, the region or market, "
            "RAM / storage, or the carrier."
        )
    value = clean_name(name, "Variant name") if name.strip() else identity.label()
    text = clean_details(details)
    check_rate(user)
    _refuse_existing(
        check_existing("variant", value, model=model, code=identity.model_code),
        "Variant",
    )
    try:
        with transaction.atomic():
            return VariantCreationRequest.objects.create(
                model=model,
                name=value[:NAME_MAX],
                model_code=identity.model_code,
                region=identity.region,
                memory=identity.memory,
                carrier=identity.carrier,
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
    """Request a chipset: vendor and name are required, aliases are optional."""
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
    check_rate(user)
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
    refuse_existing_firmware(model, firmware_type)

    key = {
        "brand": brand,
        "model": model,
        "variant_name__iexact": variant,
        "firmware_type": firmware_type,
    }
    existing = FirmwareRequest.objects.filter(**key).first()
    if existing is not None:
        return _support_request(user, existing), False
    check_rate(user)
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
    """The valid urgency values with their labels."""
    return list(FIRMWARE_REQUEST_URGENCIES)


def refuse_existing_firmware(model: Model | None, firmware_type: str) -> None:
    """Refuse a firmware request when that firmware is already published."""
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
# "My requests"
# ---------------------------------------------------------------------------


@dataclass
class MyRequests:
    """A user's own requests of every kind, newest first within each kind."""

    brands: list[BrandCreationRequest]
    models: list[ModelCreationRequest]
    variants: list[VariantCreationRequest]
    chipsets: list[ChipsetCreationRequest]
    firmware: list[FirmwareRequest]

    @property
    def total(self) -> int:
        """How many requests the user has filed or supports."""
        return (
            len(self.brands)
            + len(self.models)
            + len(self.variants)
            + len(self.chipsets)
            + len(self.firmware)
        )


def my_requests(user: Any) -> MyRequests:
    """Everything ``user`` requested, with its status and reviewer."""
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
