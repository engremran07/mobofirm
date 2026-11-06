"""Generic model-instance builder for admin page tests.

Fills every required column with a valid, unique value derived from the field type so
the admin change form of *every* registered model can be rendered without a
hand-written fixture per model. Models whose rows cannot be built generically are
listed (with the reason) in ``tests/backend/admin/test_admin_all_models.py`` and the
test asserts that list stays accurate.
"""

from __future__ import annotations

import datetime
import decimal
import itertools
import uuid
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

_counter = itertools.count(1)


class CannotBuild(Exception):
    """The model needs data this generic builder cannot invent."""


def _external_source() -> dict[str, Any]:
    return {
        "source_type": "external",
        "external_url": "https://downloads.example.invalid/firmware.zip",
    }


def _user() -> Any:
    return build(get_user_model())


def _package_tier() -> Any:
    from apps.shop.models import PackageTier

    return build(PackageTier)


# Column values that table check-constraints/clean() require; callables are evaluated
# per row so related rows are not shared between instances.
FIELD_OVERRIDES: dict[str, dict[str, Any]] = {
    "firmwares.pendingfirmware": _external_source(),
    "firmwares.officialfirmware": _external_source(),
    "firmwares.engineeringfirmware": _external_source(),
    "firmwares.readbackfirmware": _external_source(),
    "firmwares.modifiedfirmware": _external_source(),
    "firmwares.otherfirmware": _external_source(),
    "firmwares.unclassifiedfirmware": _external_source(),
    "filemanager.folderpermission": {"user": _user},
    "seo.redirect": {"source": "/old-path", "target": "/new-path"},
    "shop.product": {"package_tier": _package_tier},
    "storage.firmwarestoragelocation": {"relative_path": "firmware/file.bin"},
}


def _token(field: models.Field[Any, Any]) -> str:
    base = f"v{next(_counter)}"
    max_length = getattr(field, "max_length", None)
    return base[:max_length] if max_length else base


def _value_for(field: models.Field[Any, Any], depth: int) -> Any:
    if field.choices:
        return next(iter(field.flatchoices))[0]
    if isinstance(field, models.ForeignKey):
        return build(field.remote_field.model, depth + 1)
    if isinstance(field, models.EmailField):
        return f"user{next(_counter)}@example.test"
    if isinstance(field, models.URLField):
        return f"https://example.test/{next(_counter)}"
    if isinstance(field, models.GenericIPAddressField):
        return "127.0.0.1"
    if isinstance(field, models.UUIDField):
        return uuid.uuid4()
    if isinstance(field, models.CharField | models.TextField):
        return _token(field)
    if isinstance(field, models.BooleanField):
        return False
    if isinstance(field, models.DateTimeField):
        return timezone.now()
    if isinstance(field, models.DateField):
        return datetime.date.today()
    if isinstance(field, models.TimeField):
        return datetime.time(12, 0)
    if isinstance(field, models.DurationField):
        return datetime.timedelta(seconds=1)
    if isinstance(field, models.DecimalField):
        return decimal.Decimal(1)
    if isinstance(field, models.FloatField):
        return 1.0
    if isinstance(field, models.IntegerField):
        return 1
    if isinstance(field, models.JSONField):
        return {}
    if isinstance(field, models.BinaryField):
        return b"x"
    if isinstance(field, models.FileField):
        return ContentFile(b"x", name=f"file{next(_counter)}.txt")
    raise CannotBuild(f"no generic value for {type(field).__name__} {field.name}")


def build(model: type[models.Model], depth: int = 0) -> models.Model:
    """Create one valid row for ``model`` (and any rows it requires)."""
    if depth > 6:
        raise CannotBuild(f"required relation chain too deep at {model._meta.label}")
    singleton_getter = getattr(model, "get_solo", None)
    if callable(singleton_getter):
        instance = singleton_getter()
        if isinstance(instance, model):
            return instance
    concrete = model._meta.concrete_model
    assert concrete is not None
    values: dict[str, Any] = {}
    overrides = FIELD_OVERRIDES.get(concrete._meta.label_lower, {})
    for name, value in overrides.items():
        values[name] = value() if callable(value) else value
    for field in concrete._meta.concrete_fields:
        if field.name in values:
            continue
        if field.primary_key and (field.auto_created or field.has_default()):
            continue
        if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            continue
        if field.has_default() or field.null:
            continue
        if isinstance(field, models.CharField | models.TextField) and field.blank:
            continue
        if field.blank and field.get_default() is not None:
            continue
        if (
            isinstance(field, models.ForeignKey)
            and field.remote_field.model == concrete
        ):
            raise CannotBuild(f"{field.name} is a required self-reference")
        values[field.name] = _value_for(field, depth)
    return concrete._default_manager.create(**values)
