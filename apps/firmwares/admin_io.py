"""Import and export resources of the firmware catalogue (console model admins).

Each resource documents its columns and natural key (see
``apps.core.admin_import_export``). Rows are validated through the same services the
rest of the app uses: ``chipsets.normalize_chipset`` for chipsets, the package-format
rules by chipset vendor, ``sources.validate_external_url`` for firmware links and
``submit_external_firmware`` for firmware, so imported firmware is only ever a
``PendingFirmware`` that staff review and never lands verified.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.text import slugify

from apps.core.admin_base import (
    Column,
    Resource,
    RowError,
    parse_bool,
    split_list,
)
from apps.core.editor.sanitizer import sanitize_stored_html
from apps.core.utils.managers import default_manager

from .chipset_audit import FIRMWARE_TABLES
from .chipsets import normalize_chipset
from .constants import RESERVED_BRAND_SLUGS
from .models import (
    Brand,
    Chipset,
    FirmwarePackageFormat,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from .package_formats import device_format_error
from .services import SHA256_PATTERN, FirmwareUploadError, submit_external_firmware
from .signal_handlers import batched_blog_sync
from .sources import validate_external_url
from .submissions import file_name_from_url
from .variant_rules import variant_is_required

_URL = URLValidator(schemes=["https"])


def _text(
    raw: Mapping[str, str], name: str, *, limit: int, required: bool = False
) -> str:
    value = (raw.get(name) or "").strip()
    if required and not value:
        raise RowError(f"{name}: this column is required.")
    if len(value) > limit:
        raise RowError(f"{name}: at most {limit} characters.")
    return value


def _https_url(raw: Mapping[str, str], name: str) -> str:
    value = _text(raw, name, limit=500)
    if value:
        try:
            _URL(value)
        except ValidationError as error:
            raise RowError(f"{name}: enter a valid https URL.") from error
    return value


def _choice(
    raw: Mapping[str, str], name: str, choices: type[models.TextChoices], default: str
) -> str:
    value = (raw.get(name) or "").strip() or default
    if value not in choices.values:
        raise RowError(f"{name}: choose one of {', '.join(choices.values)}.")
    return value


def _slug(raw: Mapping[str, str], name: str, fallback: str, *, limit: int) -> str:
    value = (raw.get(name) or "").strip() or slugify(fallback)
    if not value or value != slugify(value) or len(value) > limit:
        raise RowError(
            f"{name}: use lowercase letters, digits and hyphens (max {limit})."
        )
    return value


def _differs(instance: Any, data: Mapping[str, Any], fields: Sequence[str]) -> bool:
    return any(getattr(instance, name) != data[name] for name in fields)


class _CatalogueResource(Resource):
    """Shared CRUD for resources that map columns onto model fields."""

    fields: ClassVar[tuple[str, ...]] = ()

    def create(self, data: Mapping[str, Any]) -> Any:
        return default_manager(self.model).create(
            **{name: data[name] for name in self.fields}
        )

    def update(self, instance: Any, data: Mapping[str, Any]) -> Any:
        for name in self.fields:
            setattr(instance, name, data[name])
        instance.save()
        return instance

    def changed(self, instance: Any, data: Mapping[str, Any]) -> bool:
        return _differs(instance, data, self.fields)

    def import_context(self) -> AbstractContextManager[Any]:
        return batched_blog_sync()


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


class BrandResource(_CatalogueResource):
    model = Brand
    natural_key_doc = "brand slug"
    columns = (
        Column("slug", "Brand slug; defaults to the slugified name.", key=True),
        Column("name", "Brand name (unique).", required=True),
        Column("website_url", "Official https website."),
        Column("logo_url", "https logo URL."),
        Column("description", "Short description."),
        Column("is_featured", "true or false (default false)."),
    )
    fields = ("slug", "name", "website_url", "logo_url", "description", "is_featured")

    def export_row(self, obj: Brand) -> dict[str, object]:
        return {name: getattr(obj, name) for name in self.fields}

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        name = _text(raw, "name", limit=128, required=True)
        slug = _slug(raw, "slug", name, limit=140)
        if slug in RESERVED_BRAND_SLUGS:
            raise RowError(f"slug: “{slug}” is reserved.")
        clash = Brand.objects.filter(name__iexact=name).exclude(slug=slug).first()
        if clash is not None:
            raise RowError(f"name: the brand name is used by “{clash}”.")
        return {
            "slug": slug,
            "name": name,
            "website_url": _https_url(raw, "website_url"),
            "logo_url": _https_url(raw, "logo_url"),
            "description": _text(raw, "description", limit=5000),
            "is_featured": parse_bool(
                raw.get("is_featured", ""), default=False, column="is_featured"
            ),
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["slug"],)

    def find(self, data: Mapping[str, Any]) -> Brand | None:
        return Brand.objects.filter(slug=data["slug"]).first()


# ---------------------------------------------------------------------------
# Chipsets
# ---------------------------------------------------------------------------


class ChipsetResource(_CatalogueResource):
    model = Chipset
    natural_key_doc = "chipset slug"
    columns = (
        Column("slug", "Derived from vendor and name; leave empty.", key=True),
        Column(
            "vendor",
            "Smartphone/tablet SoC vendor (MediaTek, Qualcomm, ...).",
            required=True,
        ),
        Column(
            "family", "Product family (Helio, Snapdragon, ...); detected when empty."
        ),
        Column(
            "name", "Chipset name without the vendor, e.g. Helio G99.", required=True
        ),
        Column("aliases", "Other spellings, separated by |."),
        Column("description", "Short description."),
    )
    notes = (
        "Vendors outside the phone and tablet allow-list (for example Intel) are rejected.",
    )
    fields = ("slug", "vendor", "family", "name", "aliases", "description")

    def export_row(self, obj: Chipset) -> dict[str, object]:
        return {name: getattr(obj, name) for name in self.fields}

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        vendor = _text(raw, "vendor", limit=64, required=True)
        name = _text(raw, "name", limit=128, required=True)
        normalized = normalize_chipset(f"{vendor} {name}")
        if normalized is None:
            raise RowError(
                "vendor: not a supported smartphone or tablet chipset vendor."
            )
        given = (raw.get("slug") or "").strip()
        if given and given != normalized.slug:
            raise RowError(f"slug: should be “{normalized.slug}” for this chipset.")
        return {
            "slug": normalized.slug,
            "vendor": normalized.vendor,
            "family": _text(raw, "family", limit=64) or normalized.family,
            "name": normalized.name,
            "aliases": split_list(raw.get("aliases", "")),
            "description": _text(raw, "description", limit=5000),
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["slug"],)

    def find(self, data: Mapping[str, Any]) -> Chipset | None:
        return Chipset.objects.filter(slug=data["slug"]).first()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ModelResource(_CatalogueResource):
    model = Model
    natural_key_doc = "brand and model slug (or model code)"
    columns = (
        Column(
            "brand",
            "Brand slug; the brand must already exist.",
            required=True,
            key=True,
        ),
        Column(
            "slug",
            "Model slug; defaults to the model code match or the name.",
            key=True,
        ),
        Column("name", "Model name.", required=True),
        Column("marketing_name", "Marketing name."),
        Column("model_code", "Manufacturer model code."),
        Column("codename", "Device codename."),
        Column(
            "chipset",
            "Chipset text, e.g. Qualcomm Snapdragon 680 (resolved to a chipset).",
        ),
        Column("os_version", "Operating system version text."),
        Column("release_date", "ISO date, e.g. 2024-05-01."),
        Column("status", "Market availability status."),
        Column("is_active", "true or false (default true)."),
        Column("description", "Short description."),
    )
    fields = (
        "name",
        "marketing_name",
        "model_code",
        "codename",
        "chipset",
        "os_version",
        "release_date",
        "status",
        "is_active",
        "description",
    )

    def __init__(self, user: Any) -> None:
        super().__init__(user)
        self._brands: dict[str, Brand] = {}

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.select_related("brand")

    def export_row(self, obj: Model) -> dict[str, object]:
        row: dict[str, object] = {name: getattr(obj, name) for name in self.fields}
        return {"brand": obj.brand.slug, "slug": obj.slug, **row}

    def prepare(self, rows: Sequence[Mapping[str, str]]) -> None:
        slugs = {(row.get("brand") or "").strip() for row in rows}
        self._brands = {b.slug: b for b in Brand.objects.filter(slug__in=slugs)}

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        brand_slug = _text(raw, "brand", limit=140, required=True)
        brand = self._brands.get(brand_slug)
        if brand is None:
            raise RowError(f"brand: no brand with the slug “{brand_slug}”.")
        name = _text(raw, "name", limit=128, required=True)
        model_code = _text(raw, "model_code", limit=64)
        slug = (raw.get("slug") or "").strip()
        if not slug and model_code:
            known = Model.objects.filter(brand=brand, model_code=model_code).first()
            slug = known.slug if known else ""
        slug = _slug({"slug": slug}, "slug", name, limit=160)
        chipset = _text(raw, "chipset", limit=128)
        if chipset and normalize_chipset(chipset) is None:
            raise RowError("chipset: not a supported smartphone or tablet chipset.")
        released = _text(raw, "release_date", limit=10)
        release_date = None
        if released:
            field = Model._meta.get_field("release_date")
            try:
                release_date = field.to_python(released)
            except ValidationError as error:
                raise RowError("release_date: use an ISO date (YYYY-MM-DD).") from error
        status = _text(raw, "status", limit=20) or "available"
        allowed = {
            value for value, _label in Model._meta.get_field("status").choices or ()
        }
        if status not in allowed:
            raise RowError(f"status: choose one of {', '.join(sorted(allowed))}.")
        return {
            "brand": brand,
            "slug": slug,
            "name": name,
            "marketing_name": _text(raw, "marketing_name", limit=200),
            "model_code": model_code,
            "codename": _text(raw, "codename", limit=64),
            "chipset": chipset,
            "os_version": _text(raw, "os_version", limit=128),
            "release_date": release_date,
            "status": status,
            "is_active": parse_bool(
                raw.get("is_active", ""), default=True, column="is_active"
            ),
            "description": _text(raw, "description", limit=5000),
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["brand"].slug, data["slug"])

    def label_of(self, data: Mapping[str, Any]) -> str:
        return f"{data['brand'].name} {data['name']}"

    def find(self, data: Mapping[str, Any]) -> Model | None:
        return Model.objects.filter(brand=data["brand"], slug=data["slug"]).first()

    def create(self, data: Mapping[str, Any]) -> Model:
        return Model.objects.create(
            brand=data["brand"],
            slug=data["slug"],
            **{name: data[name] for name in self.fields},
        )


# ---------------------------------------------------------------------------
# Firmware (submitted for review, never verified)
# ---------------------------------------------------------------------------


class OfficialFirmwareResource(_CatalogueResource):
    model = OfficialFirmware
    natural_key_doc = "download URL"
    columns = (
        Column(
            "external_url",
            "https download link on an approved host.",
            required=True,
            key=True,
        ),
        Column("brand", "Brand slug.", required=True),
        Column("model", "Model slug of that brand.", required=True),
        Column("variant", "Variant slug; required when the model has active variants."),
        Column("original_file_name", "File name; defaults to the end of the link."),
        Column("build_number", "Build number."),
        Column("android_version", "Android version."),
        Column(
            "package_format", "Package format valid for the model's chipset vendor."
        ),
        Column("file_hash", "SHA-256 of the file (64 hex characters)."),
        Column("file_size", "Size in bytes."),
        Column("notes", "Note for the reviewers."),
        Column("is_verified", "Export only."),
        Column("access_tier", "Export only."),
        Column("source_type", "Export only."),
    )
    notes = (
        (
            "Imported rows become pending submissions and are reviewed like any upload; "
            "they never publish verified firmware. Published firmware is never changed "
            "by an import, and stored files cannot be imported, only external links."
        ),
    )
    fields = ()

    def __init__(self, user: Any) -> None:
        super().__init__(user)
        self._brands: dict[str, Brand] = {}

    def can_create(self) -> bool:
        return bool(self.user.has_perm("firmwares.add_pendingfirmware"))

    def can_update(self) -> bool:
        return False

    def allows_update(self) -> bool:
        return False

    def import_context(self) -> AbstractContextManager[Any]:
        return batched_blog_sync()

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.select_related("brand", "model", "variant")

    def export_row(self, obj: OfficialFirmware) -> dict[str, object]:
        return {
            "external_url": obj.external_url,
            "brand": obj.brand.slug if obj.brand else "",
            "model": obj.model.slug if obj.model else "",
            "variant": obj.variant.slug if obj.variant else "",
            "original_file_name": obj.original_file_name,
            "build_number": obj.build_number,
            "android_version": obj.android_version,
            "package_format": obj.package_format,
            "file_hash": obj.file_hash,
            "file_size": obj.file_size,
            "notes": (obj.metadata or {}).get("extra_info", ""),
            "is_verified": obj.is_verified,
            "access_tier": obj.access_tier,
            "source_type": obj.source_type,
        }

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        url = _text(raw, "external_url", limit=500, required=True)
        try:
            url = validate_external_url(url)
        except (ValidationError, FirmwareUploadError) as error:
            raise RowError(
                f"external_url: {'; '.join(getattr(error, 'messages', [str(error)]))}"
            ) from error
        brand = Brand.objects.filter(
            slug=_text(raw, "brand", limit=140, required=True)
        ).first()
        if brand is None:
            raise RowError("brand: unknown brand slug.")
        model = Model.objects.filter(
            brand=brand, slug=_text(raw, "model", limit=160, required=True)
        ).first()
        if model is None:
            raise RowError("model: unknown model slug for this brand.")
        variant = None
        variant_slug = _text(raw, "variant", limit=160)
        if variant_slug:
            variant = Variant.objects.filter(model=model, slug=variant_slug).first()
            if variant is None:
                raise RowError("variant: unknown variant slug for this model.")
        elif variant_is_required(model):
            raise RowError("variant: this model has variants; name the matching one.")
        package_format = _choice(
            raw, "package_format", FirmwarePackageFormat, "unknown"
        )
        message = device_format_error(
            package_format, variant=variant, model=model, brand=brand
        )
        if message:
            raise RowError(f"package_format: {message}")
        file_hash = _text(raw, "file_hash", limit=64).lower()
        if file_hash and not SHA256_PATTERN.match(file_hash):
            raise RowError("file_hash: use a SHA-256 hex digest.")
        size_text = _text(raw, "file_size", limit=20)
        if size_text and not size_text.isdigit():
            raise RowError("file_size: use a whole number of bytes.")
        file_name = _text(raw, "original_file_name", limit=255) or file_name_from_url(
            url
        )
        if not file_name:
            raise RowError("original_file_name: the link has no file name; add one.")
        return {
            "external_url": url,
            "brand": brand,
            "model": model,
            "variant": variant,
            "original_file_name": file_name,
            "build_number": _text(raw, "build_number", limit=128),
            "android_version": _text(raw, "android_version", limit=32),
            "package_format": package_format,
            "file_hash": file_hash,
            "file_size": int(size_text) if size_text else None,
            "notes": _text(raw, "notes", limit=2000),
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["external_url"],)

    def label_of(self, data: Mapping[str, Any]) -> str:
        return data["original_file_name"]

    def find(self, data: Mapping[str, Any]) -> Any:
        url = data["external_url"]
        for table in FIRMWARE_TABLES:
            existing = table.objects.filter(external_url=url).first()
            if existing is not None:
                return existing
        return PendingFirmware.objects.filter(
            external_url=url, admin_decision="pending"
        ).first()

    def changed(self, instance: Any, data: Mapping[str, Any]) -> bool:
        return False

    def create(self, data: Mapping[str, Any]) -> PendingFirmware:
        info = {
            key: data[key] for key in ("build_number", "android_version") if data[key]
        }
        try:
            return submit_external_firmware(
                uploader=self.user,
                url=data["external_url"],
                original_file_name=data["original_file_name"],
                uploaded_brand=data["brand"],
                uploaded_model=data["model"],
                uploaded_variant=data["variant"],
                package_format=data["package_format"],
                extra_info=data["notes"] or None,
                file_size=data["file_size"],
                file_hash=data["file_hash"],
                release_info=info,
            )
        except (FirmwareUploadError, ValueError) as error:
            # Raised inside the atomic apply: the whole import rolls back.
            raise RowError(str(error)) from error


# ---------------------------------------------------------------------------
# Flashing tools, tool categories and guides
# ---------------------------------------------------------------------------

_SLUGS = re.compile(r"^[-a-z0-9_]+$")


def _slug_list(raw: Mapping[str, str], name: str) -> list[str]:
    values = split_list(raw.get(name, ""))
    bad = [value for value in values if not _SLUGS.match(value)]
    if bad:
        raise RowError(f"{name}: invalid slug “{bad[0]}”.")
    return values


class FlashingToolResource(Resource):
    model = FlashingTool
    natural_key_doc = "tool slug"
    columns = (
        Column("slug", "Tool slug; defaults to the slugified name.", key=True),
        Column("name", "Tool name.", required=True),
        Column("category", "Slug of a tool category.", required=True),
        Column("tool_type", "oem, free or open_source, or local_market."),
        Column("platform", "windows, macos, linux, android or multi."),
        Column("risk_level", "safe, moderate, advanced or risky."),
        Column("description", "Short description."),
        Column("official_url", "Publisher page (https)."),
        Column(
            "download_url",
            "Publisher download page (https); leave empty when unverified.",
        ),
        Column("version", "Current version."),
        Column("is_free", "true or false (default true)."),
        Column("is_featured", "true or false."),
        Column("is_active", "true or false (default true)."),
        Column("brands", "Supported brand slugs, separated by |."),
        Column("chipsets", "Supported chipset slugs, separated by |."),
        Column("features", "Feature bullets, separated by |."),
        Column("requirements", "Requirement bullets, separated by |."),
        Column("instructions", "Usage instructions (HTML is sanitised)."),
    )
    notes = (
        "Cracked or patched tools are not accepted; use only the publisher's own links.",
    )
    scalar = (
        "name",
        "tool_type",
        "platform",
        "risk_level",
        "description",
        "official_url",
        "download_url",
        "version",
        "is_free",
        "is_featured",
        "is_active",
        "features",
        "requirements",
        "instructions",
    )

    def import_context(self) -> AbstractContextManager[Any]:
        return batched_blog_sync()

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.select_related("category").prefetch_related(
            "supported_brands", "chipsets"
        )

    def export_row(self, obj: FlashingTool) -> dict[str, object]:
        row: dict[str, object] = {name: getattr(obj, name) for name in self.scalar}
        row.update(
            slug=obj.slug,
            category=obj.category.slug,
            brands=sorted(b.slug for b in obj.supported_brands.all()),
            chipsets=sorted(c.slug for c in obj.chipsets.all()),
        )
        return row

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        name = _text(raw, "name", limit=200, required=True)
        category = FlashingToolCategory.objects.filter(
            slug=_text(raw, "category", limit=120, required=True)
        ).first()
        if category is None:
            raise RowError("category: unknown tool category slug.")
        tool_type = _choice(raw, "tool_type", FlashingTool.ToolType, "free")
        if tool_type == FlashingTool.ToolType.CRACK:
            raise RowError("tool_type: cracked or patched tools are not accepted.")
        brands = _slug_list(raw, "brands")
        found_brands = list(Brand.objects.filter(slug__in=brands))
        if len(found_brands) != len(set(brands)):
            missing = sorted(set(brands) - {b.slug for b in found_brands})
            raise RowError(f"brands: unknown brand slug “{missing[0]}”.")
        chipsets = _slug_list(raw, "chipsets")
        found_chipsets = list(Chipset.objects.filter(slug__in=chipsets))
        if len(found_chipsets) != len(set(chipsets)):
            missing = sorted(set(chipsets) - {c.slug for c in found_chipsets})
            raise RowError(f"chipsets: unknown chipset slug “{missing[0]}”.")
        return {
            "slug": _slug(raw, "slug", name, limit=220),
            "name": name,
            "category": category,
            "tool_type": tool_type,
            "platform": _choice(raw, "platform", FlashingTool.Platform, "windows"),
            "risk_level": _choice(raw, "risk_level", FlashingTool.RiskLevel, "safe"),
            "description": _text(raw, "description", limit=5000),
            "official_url": _https_url(raw, "official_url"),
            "download_url": _https_url(raw, "download_url"),
            "version": _text(raw, "version", limit=50),
            "is_free": parse_bool(
                raw.get("is_free", ""), default=True, column="is_free"
            ),
            "is_featured": parse_bool(
                raw.get("is_featured", ""), default=False, column="is_featured"
            ),
            "is_active": parse_bool(
                raw.get("is_active", ""), default=True, column="is_active"
            ),
            "features": split_list(raw.get("features", "")),
            "requirements": split_list(raw.get("requirements", "")),
            "instructions": sanitize_stored_html(raw.get("instructions", "")),
            "brands": found_brands,
            "chipsets": found_chipsets,
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["slug"],)

    def label_of(self, data: Mapping[str, Any]) -> str:
        return data["name"]

    def find(self, data: Mapping[str, Any]) -> FlashingTool | None:
        return FlashingTool.objects.filter(slug=data["slug"]).first()

    def changed(self, instance: FlashingTool, data: Mapping[str, Any]) -> bool:
        if instance.category_id != data["category"].pk:
            return True
        if _differs(instance, data, self.scalar):
            return True
        return {b.pk for b in instance.supported_brands.all()} != {
            b.pk for b in data["brands"]
        } or {c.pk for c in instance.chipsets.all()} != {c.pk for c in data["chipsets"]}

    def _write(self, tool: FlashingTool, data: Mapping[str, Any]) -> FlashingTool:
        for name in self.scalar:
            setattr(tool, name, data[name])
        tool.slug, tool.category = data["slug"], data["category"]
        tool.save()
        tool.supported_brands.set(data["brands"])
        tool.chipsets.set(data["chipsets"])
        return tool

    def create(self, data: Mapping[str, Any]) -> FlashingTool:
        return self._write(FlashingTool(), data)

    def update(self, instance: FlashingTool, data: Mapping[str, Any]) -> FlashingTool:
        return self._write(instance, data)


class ToolCategoryResource(Resource):
    model = FlashingToolCategory
    natural_key_doc = "category slug"
    columns = (
        Column("slug", "Category slug; defaults to the slugified name.", key=True),
        Column("name", "Category name (unique).", required=True),
        Column("description", "Short description."),
        Column("icon", "Lucide icon name."),
        Column("sort_order", "Whole number."),
        Column("is_active", "true or false (default true)."),
        Column("parent", "Slug of the parent category (new general categories only)."),
        Column("kind", "Export only."),
        Column("vendor", "Export only."),
        Column("family", "Export only."),
        Column("brand", "Export only."),
        Column("os_key", "Export only."),
        Column("is_auto", "Export only."),
    )
    notes = (
        (
            "Generated categories (is_auto) are kept in sync automatically: an import can "
            "only change their description, icon, order and active flag. New categories "
            "are always general categories."
        ),
    )
    editable = ("description", "icon", "sort_order", "is_active")

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.select_related("parent", "brand")

    def export_row(self, obj: FlashingToolCategory) -> dict[str, object]:
        return {
            "slug": obj.slug,
            "name": obj.name,
            "description": obj.description,
            "icon": obj.icon,
            "sort_order": obj.sort_order,
            "is_active": obj.is_active,
            "parent": obj.parent.slug if obj.parent else "",
            "kind": obj.kind,
            "vendor": obj.vendor,
            "family": obj.family,
            "brand": obj.brand.slug if obj.brand else "",
            "os_key": obj.os_key,
            "is_auto": obj.is_auto,
        }

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        name = _text(raw, "name", limit=100, required=True)
        order = _text(raw, "sort_order", limit=9) or "0"
        if not order.isdigit():
            raise RowError("sort_order: use a whole number.")
        slug = _slug(raw, "slug", name, limit=120)
        clash = (
            FlashingToolCategory.objects.filter(name__iexact=name)
            .exclude(slug=slug)
            .first()
        )
        if clash is not None:
            raise RowError("name: another category already uses this name.")
        parent = None
        parent_slug = _text(raw, "parent", limit=120)
        if parent_slug:
            parent = FlashingToolCategory.objects.filter(slug=parent_slug).first()
            if parent is None:
                raise RowError("parent: unknown category slug.")
        return {
            "slug": slug,
            "name": name,
            "description": _text(raw, "description", limit=5000),
            "icon": _text(raw, "icon", limit=50) or "wrench",
            "sort_order": int(order),
            "is_active": parse_bool(
                raw.get("is_active", ""), default=True, column="is_active"
            ),
            "parent": parent,
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (data["slug"],)

    def label_of(self, data: Mapping[str, Any]) -> str:
        return data["name"]

    def find(self, data: Mapping[str, Any]) -> FlashingToolCategory | None:
        return FlashingToolCategory.objects.filter(slug=data["slug"]).first()

    def _fields(self, instance: FlashingToolCategory) -> tuple[str, ...]:
        if instance.is_auto:
            return self.editable
        return ("name", "parent", *self.editable)

    def changed(self, instance: FlashingToolCategory, data: Mapping[str, Any]) -> bool:
        return _differs(instance, data, self._fields(instance))

    def create(self, data: Mapping[str, Any]) -> FlashingToolCategory:
        return FlashingToolCategory.objects.create(
            kind=FlashingToolCategory.Kind.GENERAL,
            **{k: data[k] for k in ("slug", "name", "parent", *self.editable)},
        )

    def update(
        self, instance: FlashingToolCategory, data: Mapping[str, Any]
    ) -> FlashingToolCategory:
        for name in self._fields(instance):
            setattr(instance, name, data[name])
        instance.save()
        return instance


class GuideTemplateResource(Resource):
    model = FlashingGuideTemplate
    natural_key_doc = "title, guide type, brand and chipset filter"
    columns = (
        Column(
            "title_template",
            "Title with {brand}, {model}, {chipset} placeholders.",
            required=True,
            key=True,
        ),
        Column(
            "guide_type",
            "stock_flash, custom_rom, unlock_bootloader, root, recovery, downgrade, repair or general.",
            key=True,
        ),
        Column("brand", "Brand slug; empty for a generic guide.", key=True),
        Column(
            "chipset_filter", "Only models whose chipset contains this text.", key=True
        ),
        Column("body_template", "Markdown body.", required=True),
        Column("summary_template", "SEO summary."),
        Column("recommended_tools", "Tool slugs, separated by |."),
        Column(
            "auto_generate", "Must be false: guides are generated by the staff action."
        ),
        Column("submit_for_review", "true or false."),
        Column("is_active", "true or false (default true)."),
    )
    notes = (
        (
            "Importing templates never generates or publishes a post; guides are created "
            "by the generator and published through the blog workflow."
        ),
    )
    scalar = ("body_template", "summary_template", "submit_for_review", "is_active")

    def export_queryset(self, queryset: models.QuerySet) -> models.QuerySet:
        return queryset.select_related("brand").prefetch_related("recommended_tools")

    def export_row(self, obj: FlashingGuideTemplate) -> dict[str, object]:
        return {
            "title_template": obj.title_template,
            "guide_type": obj.guide_type,
            "brand": obj.brand.slug if obj.brand else "",
            "chipset_filter": obj.chipset_filter,
            "body_template": obj.body_template,
            "summary_template": obj.summary_template,
            "recommended_tools": sorted(t.slug for t in obj.recommended_tools.all()),
            "auto_generate": obj.auto_generate,
            "submit_for_review": obj.submit_for_review,
            "is_active": obj.is_active,
        }

    def clean_row(self, raw: Mapping[str, str]) -> dict[str, Any]:
        brand = None
        brand_slug = _text(raw, "brand", limit=140)
        if brand_slug:
            brand = Brand.objects.filter(slug=brand_slug).first()
            if brand is None:
                raise RowError("brand: unknown brand slug.")
        tools = _slug_list(raw, "recommended_tools")
        found = list(FlashingTool.objects.filter(slug__in=tools))
        if len(found) != len(set(tools)):
            missing = sorted(set(tools) - {t.slug for t in found})
            raise RowError(f"recommended_tools: unknown tool slug “{missing[0]}”.")
        if parse_bool(
            raw.get("auto_generate", ""), default=False, column="auto_generate"
        ):
            raise RowError("auto_generate: cannot be enabled by an import.")
        return {
            "title_template": _text(raw, "title_template", limit=300, required=True),
            "guide_type": _choice(
                raw, "guide_type", FlashingGuideTemplate.GuideType, "stock_flash"
            ),
            "brand": brand,
            "chipset_filter": _text(raw, "chipset_filter", limit=100),
            "body_template": _text(raw, "body_template", limit=50000, required=True),
            "summary_template": _text(raw, "summary_template", limit=500),
            "submit_for_review": parse_bool(
                raw.get("submit_for_review", ""),
                default=False,
                column="submit_for_review",
            ),
            "is_active": parse_bool(
                raw.get("is_active", ""), default=True, column="is_active"
            ),
            "tools": found,
        }

    def key_of(self, data: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            data["title_template"],
            data["guide_type"],
            data["brand"].slug if data["brand"] else "",
            data["chipset_filter"],
        )

    def label_of(self, data: Mapping[str, Any]) -> str:
        return data["title_template"]

    def find(self, data: Mapping[str, Any]) -> FlashingGuideTemplate | None:
        return FlashingGuideTemplate.objects.filter(
            title_template=data["title_template"],
            guide_type=data["guide_type"],
            brand=data["brand"],
            chipset_filter=data["chipset_filter"],
        ).first()

    def changed(self, instance: FlashingGuideTemplate, data: Mapping[str, Any]) -> bool:
        return _differs(instance, data, self.scalar) or {
            t.pk for t in instance.recommended_tools.all()
        } != {t.pk for t in data["tools"]}

    def _write(
        self, template: FlashingGuideTemplate, data: Mapping[str, Any]
    ) -> FlashingGuideTemplate:
        for name in (
            "title_template",
            "guide_type",
            "brand",
            "chipset_filter",
            *self.scalar,
        ):
            setattr(template, name, data[name])
        template.auto_generate = False
        template.save()
        template.recommended_tools.set(data["tools"])
        return template

    def create(self, data: Mapping[str, Any]) -> FlashingGuideTemplate:
        return self._write(FlashingGuideTemplate(), data)

    def update(
        self, instance: FlashingGuideTemplate, data: Mapping[str, Any]
    ) -> FlashingGuideTemplate:
        return self._write(instance, data)
