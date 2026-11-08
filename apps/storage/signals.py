from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from .services import delete_locations, sync_firmware_location

FIRMWARE_MODELS = (
    "firmwares.OfficialFirmware",
    "firmwares.EngineeringFirmware",
    "firmwares.ReadbackFirmware",
    "firmwares.ModifiedFirmware",
    "firmwares.OtherFirmware",
    "firmwares.UnclassifiedFirmware",
)


def sync_location_after_save(instance, **kwargs) -> None:
    """Mirror the firmware file reference once the firmware write has committed."""
    transaction.on_commit(lambda: sync_firmware_location(instance))


def drop_locations_after_delete(instance, **kwargs) -> None:
    delete_locations(instance)


for _label in FIRMWARE_MODELS:
    post_save.connect(
        sync_location_after_save, sender=_label, dispatch_uid=f"storage-save-{_label}"
    )
    post_delete.connect(
        drop_locations_after_delete,
        sender=_label,
        dispatch_uid=f"storage-delete-{_label}",
    )
