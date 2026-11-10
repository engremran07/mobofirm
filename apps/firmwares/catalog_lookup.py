"""Shared rules of the catalogue requests: errors, cleaning and existence checks.

Used by the submission module (``catalog_requests``) and the review module
(``catalog_review``): who may request, the per-user rate limit, text cleaning and
the "does it already exist?" lookups for brands, models, variants and chipsets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.text import slugify

from apps.core.editor.sanitizer import sanitize_stored_html, text_length

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
from .tracking_models import FIRMWARE_REQUEST_TYPES, FirmwareRequest
from .variant_rules import normalize_variant_code

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
    """How many requests one user may file per hour."""
    return int(getattr(settings, "FIRMWARE_REQUESTS_PER_HOUR", 10))


def has_verified_email(user: Any) -> bool:
    """True for a signed-in user whose email address is verified."""
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


def check_rate(user: Any) -> None:
    """Refuse a new request when ``user`` filed too many in the last hour."""
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
    """A whitespace-collapsed name of a valid length that has letters or digits."""
    name = " ".join((value or "").split())
    if len(name) < NAME_MIN or len(name) > NAME_MAX:
        raise CatalogRequestError(
            f"{label} must be between {NAME_MIN} and {NAME_MAX} characters."
        )
    if not slugify(name):
        raise CatalogRequestError(f"{label} must contain letters or numbers.")
    return name


def clean_details(value: str) -> str:
    """The free-text details: sanitised, trimmed and length-checked on visible text."""
    details = sanitize_stored_html(value or "").strip()
    if text_length(details) > DETAILS_MAX:
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
        """True when the name exists or a request for it is already waiting."""
        return self.exact is not None or bool(self.pending)


def brand_query(name: str) -> Q:
    """Match a brand by name (any case) or slug."""
    key = slugify(name)
    return Q(name__iexact=name) | Q(slug=key)


def find_brand(name: str) -> Brand | None:
    """The brand called ``name`` (any case, or by slug), if any."""
    return Brand.objects.filter(brand_query(name)).first()


def model_query(name: str) -> Q:
    """Match a model by name, marketing name, slug or model code."""
    key = slugify(name)
    return (
        Q(name__iexact=name)
        | Q(marketing_name__iexact=name)
        | Q(slug=key)
        | Q(model_code__iexact=name)
    )


def find_model(brand: Brand, name: str) -> Model | None:
    """The model of ``brand`` matching ``name``, if any."""
    return Model.objects.filter(model_query(name), brand=brand).first()


def variant_query(name: str, code: str = "") -> Q:
    """Match a variant by name, slug or code; matches nothing when both are blank."""
    query = Q(pk__in=[])
    if name:
        query |= (
            Q(name__iexact=name) | Q(slug=slugify(name)) | Q(model_code__iexact=name)
        )
    if code:
        query |= Q(model_code__iexact=code)
    return query


def find_variant(model: Model, name: str, code: str = "") -> Variant | None:
    """The variant of ``model`` with this name, slug or (normalised) code."""
    return Variant.objects.filter(variant_query(name, code), model=model).first()


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
    code: str = "",
) -> ExistenceCheck:
    """Exact and similar catalogue matches plus pending requests for ``name``.

    ``code`` (variants only) is matched against the normalised variant codes of
    the model as well, so ``ai2401_a`` finds ``AI2401_A``.
    """
    value = " ".join((name or "").split())
    variant_code = normalize_variant_code(code) if kind == "variant" else ""
    if len(value) < 2 and not variant_code:
        return ExistenceCheck()
    if kind == "brand":
        return _check_brand(value)
    if kind == "chipset":
        return _check_chipset(" ".join((vendor or "").split()), value)
    if kind == "model":
        return _check_model(brand, value) if brand is not None else ExistenceCheck()
    if model is None:
        return ExistenceCheck()
    return _check_variant(model, value, variant_code)


def _loose(value: str) -> Q:
    """Names or slugs that merely contain ``value`` (never empty)."""
    key = slugify(value)
    return Q(name__icontains=value) | (Q(slug__icontains=key) if key else Q())


def _result(
    exact: object | None, similar: QuerySet[Any], pending: QuerySet[Any]
) -> ExistenceCheck:
    return ExistenceCheck(
        exact=exact,
        similar=list(similar[:SUGGESTION_LIMIT]),
        pending=list(pending[:SUGGESTION_LIMIT]),
    )


def _check_brand(value: str) -> ExistenceCheck:
    return _result(
        find_brand(value),
        Brand.objects.filter(_loose(value)).order_by("name"),
        BrandCreationRequest.objects.filter(status="pending", name__iexact=value),
    )


def _check_chipset(vendor: str, value: str) -> ExistenceCheck:
    similar = Chipset.objects.filter(
        _loose(value) | Q(family__icontains=value)
    ).order_by("vendor", "name")
    return _result(
        find_chipset(vendor, value),
        similar,
        ChipsetCreationRequest.objects.filter(status="pending", name__iexact=value),
    )


def _check_model(brand: Brand, value: str) -> ExistenceCheck:
    similar = Model.objects.filter(
        _loose(value)
        | Q(marketing_name__icontains=value)
        | Q(model_code__icontains=value),
        brand=brand,
    ).order_by("name")
    pending = ModelCreationRequest.objects.filter(
        status="pending", brand=brand, name__iexact=value
    )
    return _result(find_model(brand, value), similar, pending)


def _check_variant(model: Model, value: str, code: str) -> ExistenceCheck:
    match_similar = Q(model_code__icontains=code) if code else Q()
    match_pending = Q(model_code__iexact=code) if code else Q()
    if value:
        match_similar |= _loose(value) | Q(model_code__icontains=value)
        match_pending |= Q(name__iexact=value)
    similar = Variant.objects.filter(match_similar, model=model).order_by("name")
    pending = VariantCreationRequest.objects.filter(
        match_pending, status="pending", model=model
    )
    return _result(find_variant(model, value, code), similar, pending)
