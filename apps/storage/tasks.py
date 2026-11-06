from __future__ import annotations

from celery import shared_task

from .models import FirmwareStorageLocation
from .services import sync_all_firmware, verify_location

VERIFY_BATCH_SIZE = 200


@shared_task(name="apps.storage.tasks.verify_storage_locations")
def verify_storage_locations() -> dict[str, int]:
    """Re-verify every storage location; safe to run repeatedly."""
    counts: dict[str, int] = {}
    queryset = FirmwareStorageLocation.objects.order_by("last_verified_at", "pk")
    for location in queryset.iterator(chunk_size=VERIFY_BATCH_SIZE):
        verified = verify_location(location)
        counts[verified.status] = counts.get(verified.status, 0) + 1
    return counts


@shared_task(name="apps.storage.tasks.sync_firmware_locations")
def sync_firmware_locations() -> dict[str, int]:
    """Backfill location rows for firmware saved before the app was installed."""
    report = sync_all_firmware()
    return {
        "firmware_seen": report.firmware_seen,
        "locations_synced": report.locations_synced,
        "unmanaged": report.unmanaged,
    }
