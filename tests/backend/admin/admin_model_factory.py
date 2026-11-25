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
from collections.abc import Callable
from typing import Any

from django.apps import apps as django_apps
from django.contrib import admin
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

from apps.core.utils.managers import default_manager

_counter = itertools.count(1)


class CannotBuild(Exception):
    """The model needs data this generic builder cannot invent."""


def _external_source() -> dict[str, Any]:
    return {
        "source_type": "external",
        "external_url": "https://downloads.example.invalid/firmware.zip",
    }


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
        # Unique integers (e.g. seeded level numbers) must not collide with seed rows.
        return next(_counter) + 10_000 if field.unique else 1
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
    return default_manager(concrete).create(**values)


def registered_admins() -> dict[type[models.Model], Any]:
    """Every model registered on the admin site mapped to its ModelAdmin (public API)."""
    return {
        model: admin.site.get_model_admin(model)
        for model in django_apps.get_models()
        if admin.site.is_registered(model)
    }


def _automated_blog_link() -> models.Model:
    """The table's check constraint ties ``kind`` to exactly one target column.

    Creating the brand already makes the automation own its post (one per brand), so the
    existing link is the row to use; a manual link would violate the unique brand key.
    """
    from apps.blog.models import Post
    from apps.firmwares.models import AutomatedBlogLink, Brand

    brand = build(Brand)
    assert isinstance(brand, Brand)
    existing = AutomatedBlogLink.objects.filter(
        kind=AutomatedBlogLink.Kind.BRAND, brand=brand
    ).first()
    if existing is not None:
        return existing
    post = build(Post)
    assert isinstance(post, Post)
    return AutomatedBlogLink.objects.create(
        kind=AutomatedBlogLink.Kind.BRAND, brand=brand, post=post
    )


def _firmware_shortener_override() -> models.Model:
    """The override names a real firmware by type and id, which ``clean`` verifies."""
    from apps.firmwares.catalog import PUBLIC_FIRMWARE_TYPES
    from apps.firmwares.models import OfficialFirmware
    from apps.firmwares.shortener_models import FirmwareShortenerOverride

    firmware = build(OfficialFirmware)
    assert isinstance(firmware, OfficialFirmware)
    firmware_type = next(
        key for key, model in PUBLIC_FIRMWARE_TYPES if model is OfficialFirmware
    )
    return FirmwareShortenerOverride.objects.create(
        firmware_type=firmware_type, firmware_id=firmware.pk
    )


def _editor_profile() -> models.Model:
    """One profile per context is a unique key, and the data migration seeds them all.

    Reuse the seeded row when migrations ran; seed once when they did not.
    """
    from apps.site_settings.editor_models import EditorProfile

    row = EditorProfile.objects.order_by("pk").first()
    if row is None:
        EditorProfile.seed()
        row = EditorProfile.objects.order_by("pk").first()
    assert row is not None
    return row


def _download_token() -> models.Model:
    """Build a token attached to a real firmware target for GenericForeignKey reads."""
    from django.contrib.contenttypes.models import ContentType

    from apps.firmwares.models import DownloadToken, OfficialFirmware

    firmware = build(OfficialFirmware)
    assert isinstance(firmware, OfficialFirmware)
    return DownloadToken.objects.create(
        content_type=ContentType.objects.get_for_model(firmware),
        object_id=firmware.pk,
        expires_at=timezone.now() + datetime.timedelta(hours=1),
    )


def _catalog_update_proposal() -> models.Model:
    """Build the exactly-one-target proposal constraint with a real brand target."""
    from apps.firmwares.models import Brand, CatalogUpdateProposal
    from apps.users.models import CustomUser

    brand = build(Brand)
    assert isinstance(brand, Brand)
    submitted_by = build(django_apps.get_model("users", "customuser"))
    assert isinstance(submitted_by, CustomUser)
    return CatalogUpdateProposal.objects.create(
        target_kind=CatalogUpdateProposal.TargetKind.BRAND,
        brand=brand,
        submitted_by=submitted_by,
        base_values={"description": brand.description},
        proposed_values={"description": "Admin smoke-test proposal"},
    )


def _wallet_repair() -> models.Model:
    """Build an append-only repair row that satisfies its changed-balance constraint."""
    from apps.wallet.models import Wallet, WalletRepair

    wallet = build(Wallet)
    assert isinstance(wallet, Wallet)
    return WalletRepair.objects.create(
        wallet=wallet,
        previous_balance=0,
        repaired_balance=1,
        previous_held=0,
        repaired_held=0,
        reason="Admin smoke-test fixture",
        key=f"admin-smoke-{next(_counter)}",
    )


# Builders for models whose columns depend on each other (constraints), which the
# generic field-type builder cannot satisfy.
CUSTOM_BUILDERS: dict[str, Callable[[], models.Model]] = {
    "firmwares.automatedbloglink": _automated_blog_link,
    "firmwares.catalogupdateproposal": _catalog_update_proposal,
    "firmwares.firmwareshorteneroverride": _firmware_shortener_override,
    "firmwares.downloadtoken": _download_token,
    "site_settings.editorprofile": _editor_profile,
    "wallet.walletrepair": _wallet_repair,
}


def build_row(model: type[models.Model]) -> models.Model:
    """Create one row, using the model's custom builder when it has one."""
    custom = CUSTOM_BUILDERS.get(model._meta.label_lower)
    return custom() if custom else build(model)


def admin_pk(instance: models.Model) -> object:
    """DRF's token admin addresses a token by its user's id, not by the key."""
    if instance._meta.label_lower == "authtoken.token":
        return getattr(instance, "user_id", instance.pk)
    return instance.pk
