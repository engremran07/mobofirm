"""Public firmware catalogue queries shared by views, sitemaps and blog automation.

A firmware record is public only when it is active and ``is_verified`` (reviewed and
approved). Every listing, count, search, sitemap and API surface goes through
:func:`get_public_firmware_queryset` so the review boundary cannot be bypassed.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Count, Q, QuerySet

from .models import (
    BaseFirmware,
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
)

PUBLIC_FIRMWARE_TYPES: tuple[tuple[str, type[BaseFirmware]], ...] = (
    ("official", OfficialFirmware),
    ("engineering", EngineeringFirmware),
    ("readback", ReadbackFirmware),
    ("modified", ModifiedFirmware),
    ("other", OtherFirmware),
    ("unclassified", UnclassifiedFirmware),
)

FIRMWARE_TYPE_DISPLAY = {
    "official": "Official",
    "engineering": "Engineering",
    "readback": "Readback",
    "modified": "Modified",
    "other": "Other",
    "unclassified": "Unclassified",
}

FIRMWARE_TYPES: dict[str, type[BaseFirmware]] = {}
for _key, _cls in PUBLIC_FIRMWARE_TYPES:
    FIRMWARE_TYPES[_key] = _cls
    FIRMWARE_TYPES[_cls.__name__.lower()] = _cls
    FIRMWARE_TYPE_DISPLAY[_cls.__name__.lower()] = FIRMWARE_TYPE_DISPLAY[_key]

TYPE_KEY_BY_CLASS = {cls: key for key, cls in PUBLIC_FIRMWARE_TYPES}


def get_firmware_model(firmware_type: str) -> type[BaseFirmware] | None:
    """Get the firmware model class from a type string."""
    return FIRMWARE_TYPES.get((firmware_type or "").lower())


def get_public_firmware_queryset(model_class: type[BaseFirmware]) -> QuerySet:
    """Active firmware that has been reviewed and approved."""
    return model_class.objects.filter(is_active=True, is_verified=True)


def label_firmware(firmware: Any, type_key: str) -> Any:
    firmware.firmware_type = type_key
    firmware.firmware_type_display = FIRMWARE_TYPE_DISPLAY[type_key]
    return firmware


def chipset_filter(chipset) -> Q:
    """Firmware whose model or variant uses the given Chipset."""
    return Q(model__chipset_ref=chipset) | Q(variant__chipset_ref=chipset)


def get_public_firmwares(*conditions: Q, **filters: Any) -> list:
    """Public firmware of every type matching the filters, newest first."""
    firmwares: list = []
    for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
        queryset = (
            get_public_firmware_queryset(model_class)
            .filter(*conditions, **filters)
            .select_related("brand", "model", "variant")
        )
        firmwares.extend(label_firmware(fw, type_key) for fw in queryset)
    firmwares.sort(key=lambda fw: fw.created_at, reverse=True)
    return firmwares


def get_all_firmwares_for_model(model_obj: Model) -> list:
    """Active, approved firmware of every type for one device model."""
    return get_public_firmwares(model=model_obj)


def group_by_category(firmwares: list) -> list[tuple[str, str, list]]:
    """Group labelled firmware into ``(type_key, label, items)`` for every category."""
    buckets: dict[str, list] = defaultdict(list)
    for firmware in firmwares:
        buckets[firmware.firmware_type].append(firmware)
    return [
        (key, FIRMWARE_TYPE_DISPLAY[key], buckets[key])
        for key, _cls in PUBLIC_FIRMWARE_TYPES
        if buckets[key]
    ]


def public_counts(field: str, *conditions: Q, **filters: Any) -> dict[Any, int]:
    """Public firmware counts grouped by ``field`` using one query per category."""
    totals: dict[Any, int] = defaultdict(int)
    for _key, model_class in PUBLIC_FIRMWARE_TYPES:
        rows = (
            get_public_firmware_queryset(model_class)
            .filter(*conditions, **filters)
            .exclude(**{f"{field}__isnull": True})
            .order_by()
            .values(field)
            .annotate(total=Count("pk"))
        )
        for row in rows:
            totals[row[field]] += row["total"]
    return totals


def public_count_by_type(*conditions: Q, **filters: Any) -> dict[str, int]:
    """Public firmware counts per category key."""
    return {
        key: get_public_firmware_queryset(model_class)
        .filter(*conditions, **filters)
        .count()
        for key, model_class in PUBLIC_FIRMWARE_TYPES
    }


def total_public_firmware(*conditions: Q, **filters: Any) -> int:
    return sum(public_count_by_type(*conditions, **filters).values())


def format_file_size(size: int | None) -> str:
    if not size:
        return "Unknown"
    if size >= 1073741824:
        return f"{size / 1073741824:.2f} GB"
    if size >= 1048576:
        return f"{size / 1048576:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size} bytes"
