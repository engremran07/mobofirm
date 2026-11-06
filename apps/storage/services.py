"""Register and verify storage locations for typed firmware records."""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Model, QuerySet, Sum
from django.utils import timezone

from . import providers
from .models import FirmwareStorageLocation

logger = logging.getLogger(__name__)

FIRMWARE_MODEL_NAMES = (
    "OfficialFirmware",
    "EngineeringFirmware",
    "ReadbackFirmware",
    "ModifiedFirmware",
    "OtherFirmware",
    "UnclassifiedFirmware",
)
HASH_CHUNK_BYTES = 1024 * 1024


def firmware_models() -> list[type[Model]]:
    return [apps.get_model("firmwares", name) for name in FIRMWARE_MODEL_NAMES]


def locations_for(firmware: Any) -> QuerySet[FirmwareStorageLocation]:
    content_type = ContentType.objects.get_for_model(firmware, for_concrete_model=False)
    return FirmwareStorageLocation.objects.filter(
        content_type=content_type, object_id=str(firmware.pk)
    )


def _upsert_location(
    lookup: dict[str, Any], provider: str, **target: str
) -> FirmwareStorageLocation:
    """Create the row, or reset verification state only when the target changed."""
    location, created = FirmwareStorageLocation.objects.get_or_create(
        provider=provider, **lookup, defaults=target
    )
    if not created and any(
        getattr(location, key) != value for key, value in target.items()
    ):
        for key, value in target.items():
            setattr(location, key, value)
        location.status = FirmwareStorageLocation.Status.PENDING
        location.sha256 = ""
        location.size_bytes = None
        location.last_error = ""
        location.save()
    return location


def sync_firmware_location(firmware: Any) -> FirmwareStorageLocation | None:
    """Create or update the location row that mirrors the firmware's file source.

    External firmware (``external_url``) maps to the external-link provider, stored
    firmware (``stored_file_path``) to the local provider. Returns ``None`` when the
    source is empty or cannot be managed (a path outside the storage root, or a link
    on a host that is not allow-listed). Idempotent: repeated calls converge on one
    row per provider.
    """
    external_url = (getattr(firmware, "external_url", "") or "").strip()
    stored = (firmware.stored_file_path or "").strip()
    content_type = ContentType.objects.get_for_model(firmware, for_concrete_model=False)
    lookup = {"content_type": content_type, "object_id": str(firmware.pk)}

    if external_url:
        if not providers.external_host_allowed(external_url):
            logger.warning(
                "Firmware %s link host is not allow-listed; no location recorded.",
                firmware.pk,
            )
            return None
        return _upsert_location(
            lookup,
            providers.EXTERNAL_LINK,
            external_url=external_url,
            relative_path="",
        )
    if not stored:
        return None
    try:
        relative = providers.relative_to_root(stored)
    except providers.StoragePathError:
        logger.warning(
            "Firmware %s path is outside the storage root; no location recorded.",
            firmware.pk,
        )
        return None
    return _upsert_location(
        lookup, providers.LOCAL, relative_path=relative, external_url=""
    )


def _sha256_of(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _expected_hash(location: FirmwareStorageLocation) -> str:
    firmware = location.firmware
    return (getattr(firmware, "file_hash", "") or "").strip().lower()


def verify_location(location: FirmwareStorageLocation) -> FirmwareStorageLocation:
    """Check stored bytes (local) or link policy (external) and record the result."""
    status = FirmwareStorageLocation.Status.AVAILABLE
    error = ""

    if location.provider == providers.LOCAL:
        try:
            path = providers.resolve_local_path(location.relative_path)
        except providers.StoragePathError:
            status, error = FirmwareStorageLocation.Status.MISSING, "Unsafe path."
        else:
            if not path.is_file():
                status = FirmwareStorageLocation.Status.MISSING
                error = "File not found on disk."
            else:
                location.sha256, location.size_bytes = _sha256_of(path)
                expected = _expected_hash(location)
                if expected and expected != location.sha256:
                    status = FirmwareStorageLocation.Status.MISMATCH
                    error = "SHA-256 differs from the firmware record."
    elif location.provider == providers.EXTERNAL_LINK:
        if not providers.external_host_allowed(location.external_url):
            status = FirmwareStorageLocation.Status.DISABLED
            error = "Host is not allow-listed."
    else:
        status = FirmwareStorageLocation.Status.DISABLED
        error = "Provider is disabled."

    location.status = status
    location.last_error = error
    location.last_verified_at = timezone.now()
    location.save()
    return location


def delete_locations(firmware: Any) -> int:
    deleted, _ = locations_for(firmware).delete()
    return deleted


@dataclass(frozen=True)
class SyncReport:
    firmware_seen: int
    locations_synced: int
    unmanaged: int


def sync_all_firmware() -> SyncReport:
    seen = synced = 0
    for model_class in firmware_models():
        for firmware in model_class._default_manager.iterator():
            seen += 1
            with transaction.atomic():
                if sync_firmware_location(firmware) is not None:
                    synced += 1
    return SyncReport(seen, synced, seen - synced)


@dataclass(frozen=True)
class DiskUsage:
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> int:
        return round(self.used / self.total * 100) if self.total else 0


def disk_usage() -> DiskUsage | None:
    """Capacity of the volume holding the firmware storage root, if it exists."""
    try:
        usage = shutil.disk_usage(providers.storage_root())
    except OSError:
        return None
    return DiskUsage(usage.total, usage.used, usage.free)


@dataclass(frozen=True)
class StatusCount:
    value: str
    label: str
    total: int

    @property
    def text(self) -> str:
        return f"{self.label}: {self.total}"


@dataclass(frozen=True)
class StorageOverview:
    total: int
    by_status: list[StatusCount]
    by_provider: list[tuple[str, int]]
    managed_bytes: int
    healthy_percent: int
    disk: DiskUsage | None

    @property
    def healthy_text(self) -> str:
        return f"{self.healthy_percent}%"


def status_overview() -> StorageOverview:
    """Counts and capacity figures for the staff storage status page."""
    status_counts = dict(
        FirmwareStorageLocation.objects.values_list("status").annotate(
            total=Count("pk")
        )
    )
    provider_counts = dict(
        FirmwareStorageLocation.objects.values_list("provider").annotate(
            total=Count("pk")
        )
    )
    total = sum(status_counts.values())
    available = status_counts.get(FirmwareStorageLocation.Status.AVAILABLE, 0)
    managed = FirmwareStorageLocation.objects.filter(
        status=FirmwareStorageLocation.Status.AVAILABLE
    ).aggregate(size=Sum("size_bytes"))["size"]
    return StorageOverview(
        total=total,
        by_status=[
            StatusCount(value, label, status_counts.get(value, 0))
            for value, label in FirmwareStorageLocation.Status.choices
        ],
        by_provider=[
            (label, provider_counts.get(value, 0))
            for value, label in providers.PROVIDER_CHOICES
        ],
        managed_bytes=managed or 0,
        healthy_percent=round(available / total * 100) if total else 100,
        disk=disk_usage(),
    )
