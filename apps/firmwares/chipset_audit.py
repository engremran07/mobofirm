"""Audit and prune chipset rows outside the smartphone/tablet SoC allow-list.

The catalogue only covers phone and tablet SoCs (see
:data:`apps.firmwares.chipsets.SUPPORTED_CHIPSET_VENDORS`). Chipset rows of any other
vendor (for example Intel) were created by older free-text resolution. This module
reports them with everything that references them and removes only the rows nothing
references; a referenced row is never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from .chipsets import is_supported_vendor
from .models import (
    AutomatedBlogLink,
    Chipset,
    EngineeringFirmware,
    FlashingTool,
    Model,
    ModelCreationRequest,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
    Variant,
)

FIRMWARE_TABLES = (
    OfficialFirmware,
    EngineeringFirmware,
    ReadbackFirmware,
    ModifiedFirmware,
    OtherFirmware,
    UnclassifiedFirmware,
)


@dataclass
class ChipsetUsage:
    """One unsupported chipset row and the rows that reference it."""

    chipset: Chipset
    references: dict[str, int] = field(default_factory=dict)

    @property
    def total_references(self) -> int:
        return sum(self.references.values())

    @property
    def is_referenced(self) -> bool:
        return self.total_references > 0


def _references(chipset: Chipset) -> dict[str, int]:
    counts = {
        "models": Model.objects.filter(chipset_ref=chipset).count(),
        "variants": Variant.objects.filter(chipset_ref=chipset).count(),
        "model requests": ModelCreationRequest.objects.filter(chipset=chipset).count(),
        "tools": FlashingTool.objects.filter(chipsets=chipset).count(),
        "automated posts": AutomatedBlogLink.objects.filter(chipset=chipset).count(),
        "firmwares": sum(
            table.objects.filter(model__chipset_ref=chipset).count()
            + table.objects.filter(variant__chipset_ref=chipset).count()
            for table in FIRMWARE_TABLES
        ),
    }
    return {name: count for name, count in counts.items() if count}


def unsupported_chipsets() -> list[ChipsetUsage]:
    """Chipset rows whose vendor is outside the allow-list, with their references."""
    return [
        ChipsetUsage(chipset, _references(chipset))
        for chipset in Chipset.objects.select_related("vendor").order_by(
            "vendor__name", "name"
        )
        if not is_supported_vendor(chipset.vendor.name)
    ]


def prune_unreferenced(usages: list[ChipsetUsage]) -> int:
    """Delete the unsupported chipset rows nothing references; return how many.

    Referenced rows are left untouched. Each row is re-checked inside the
    transaction so a reference created after the audit keeps its row.
    """
    deleted = 0
    with transaction.atomic():
        for usage in usages:
            if usage.is_referenced:
                continue
            chipset = Chipset.objects.select_for_update().filter(pk=usage.chipset.pk)
            if chipset.first() is None or _references(usage.chipset):
                continue
            chipset.delete()
            deleted += 1
    return deleted
