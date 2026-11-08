"""Bounty targets: firmware requests, models and variants.

The catalogue is reached only through the app registry and ContentType at call
time (no model imports), so bounty stays installable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.urls import NoReverseMatch, reverse

KIND_REQUEST: Final = "request"
KIND_MODEL: Final = "model"
KIND_VARIANT: Final = "variant"
KIND_GUIDE: Final = "guide"

TARGET_MODELS: Final[dict[str, tuple[str, str]]] = {
    KIND_REQUEST: ("firmwares", "firmwarerequest"),
    KIND_MODEL: ("firmwares", "model"),
    KIND_VARIANT: ("firmwares", "variant"),
    KIND_GUIDE: ("blog", "guiderequest"),
}
CATALOGUE_KINDS: Final = (KIND_REQUEST, KIND_MODEL, KIND_VARIANT)
OPEN_REQUEST_STATUSES: Final = ("open", "in_progress")
OPEN_GUIDE_STATUSES: Final = ("approved",)


class TargetError(ValueError):
    """The thing a bounty should be attached to cannot be used."""


@dataclass(frozen=True, slots=True)
class Target:
    kind: str
    content_type: ContentType
    object_id: int
    label: str
    url: str


def catalogue_installed() -> bool:
    return apps.is_installed("apps.firmwares")


def _model_for(kind: str) -> Any:
    try:
        app_label, model_name = TARGET_MODELS[kind]
    except KeyError as error:
        raise TargetError(
            "Choose a firmware request, model, variant or guide request."
        ) from error
    if kind in CATALOGUE_KINDS and not catalogue_installed():
        raise TargetError("The firmware catalogue is not available.")
    if kind == KIND_GUIDE and not apps.is_installed("apps.blog"):
        raise TargetError("Guide requests are not available.")
    return apps.get_model(app_label, model_name)


def content_type_for(kind: str) -> ContentType:
    return ContentType.objects.get_for_model(_model_for(kind))


def kind_of(content_type: ContentType) -> str:
    for kind, (app_label, model_name) in TARGET_MODELS.items():
        if (content_type.app_label, content_type.model) == (app_label, model_name):
            return kind
    return ""


def _label(kind: str, obj: Any) -> str:
    if kind == KIND_GUIDE:
        return str(obj.title)
    if kind == KIND_REQUEST:
        parts = [
            obj.brand.name,
            obj.model.name if obj.model_id else "",
            obj.variant_name,
        ]
        text = " ".join(part for part in parts if part)
        return f"{text} ({obj.firmware_type} firmware)"
    if kind == KIND_MODEL:
        return f"{obj.brand.name} {obj.name}"
    return f"{obj.model.brand.name} {obj.model.name} · {obj.name}"


def _url(kind: str, obj: Any) -> str:
    if kind == KIND_GUIDE:
        return str(obj.get_absolute_url())
    model = obj.model if kind == KIND_VARIANT else obj
    if kind == KIND_REQUEST:
        model = obj.model if obj.model_id else None
    if model is None:
        return ""
    try:
        return reverse(
            "firmwares:model_detail",
            kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
        )
    except NoReverseMatch:
        return ""


def resolve(kind: str, object_id: int) -> Target:
    """Load and validate a bounty target; raises ``TargetError`` with a user message."""
    model = _model_for(kind)
    queryset = model.objects.all()
    if kind == KIND_REQUEST:
        queryset = queryset.select_related("brand", "model")
    elif kind == KIND_MODEL:
        queryset = queryset.select_related("brand")
    elif kind == KIND_VARIANT:
        queryset = queryset.select_related("model__brand")
    obj = queryset.filter(pk=object_id).first()
    if obj is None:
        raise TargetError("That item does not exist.")
    if kind == KIND_REQUEST and obj.status not in OPEN_REQUEST_STATUSES:
        raise TargetError("That firmware request is no longer open.")
    if kind == KIND_GUIDE and obj.status not in OPEN_GUIDE_STATUSES:
        raise TargetError("Only an approved guide request can carry a bounty.")
    return Target(
        kind=kind,
        content_type=ContentType.objects.get_for_model(model),
        object_id=int(obj.pk),
        label=_label(kind, obj)[:200],
        url=_url(kind, obj),
    )
