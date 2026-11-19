"""What the shop keeps and drops when a firmware file is deleted (F-019).

The shop refers to firmware through ``ContentType`` + UUID (the typed firmware tables
belong to ``apps.firmwares``), so no foreign key cascades when a file is deleted. Every
shop model that holds such a reference has a declared disposition in
:data:`FILE_REFERENCES`; a test fails when a new generic reference is added without one.

* ``QuotaConsumption`` and ``AdUnlockTicket`` (with its steps) describe how a *file* was
  served or unlocked; once the file is gone nothing can use them, so they are purged.
  The user's usage meters shrink by the deleted file: that is the documented outcome of
  a staff deletion, never of a download.
* ``OrderItem`` is a financial record (what was bought and for how much, with the file
  name snapshotted on the line); it is retained, and the library listing skips lines
  whose file no longer exists.

The purge runs from the firmware ``post_delete`` signal. Receivers are attached by
model label, so the shop never imports the firmware app; the label list is verified
against the firmware app's own sender list in ``tests/backend/integrations``.
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete

from .models import QuotaConsumption
from .unlock_models import AdUnlockTicket

logger = logging.getLogger(__name__)

PURGED = "purged"
RETAINED = "retained"

#: Disposition of each shop model that references a file generically.
FILE_REFERENCES: dict[str, str] = {
    "QuotaConsumption": PURGED,
    "AdUnlockTicket": PURGED,
    "OrderItem": RETAINED,
}

#: The typed firmware models whose deletion removes a file.
FIRMWARE_MODEL_LABELS: tuple[str, ...] = (
    "firmwares.OfficialFirmware",
    "firmwares.EngineeringFirmware",
    "firmwares.ReadbackFirmware",
    "firmwares.ModifiedFirmware",
    "firmwares.OtherFirmware",
    "firmwares.UnclassifiedFirmware",
)

RECEIVER_UID = "shop_purge_file_references_v1"


def purge_file_references(firmware: Any) -> dict[str, int]:
    """Delete the unlock tickets and quota rows of a deleted file; returns the counts.

    Idempotent: a second run (or a file nobody used) deletes nothing.
    """
    content_type = ContentType.objects.get_for_model(firmware, for_concrete_model=False)
    ref = {"content_type": content_type, "object_id": firmware.pk}
    tickets = AdUnlockTicket.objects.filter(**ref).delete()[0]
    consumed = QuotaConsumption.objects.filter(**ref).delete()[0]
    return {"unlock_rows": tickets, "quota_rows": consumed}


def _on_firmware_deleted(sender: Any, instance: Any, **kwargs: Any) -> None:
    counts = purge_file_references(instance)
    if any(counts.values()):
        logger.info("Purged shop rows of deleted file %s: %s", instance.pk, counts)


def connect_firmware_receivers() -> None:
    """Attach the purge to every firmware model (lazy: unknown labels never fire)."""
    for label in FIRMWARE_MODEL_LABELS:
        post_delete.connect(
            _on_firmware_deleted,
            sender=label,
            dispatch_uid=f"{RECEIVER_UID}:{label}",
        )
