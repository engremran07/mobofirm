"""Clean-up of everything that references a deleted firmware (F-019).

Firmware rows are referenced by typed references (see :mod:`apps.firmwares.references`);
none of them is a database foreign key, so nothing cascades. The firmware
``post_delete`` signal calls :func:`purge_firmware_references`, the one place that
knows every table.
A test deletes a file of each type with a row in every table and fails when a new
live-reference table is not cleaned here. Append-only reports keep a filename
snapshot so staff can retain the audit history after a firmware row is removed.
"""

from __future__ import annotations

from typing import Any

from .access_change_models import AccessChangeRequest
from .models import DownloadToken, ServedDownload
from .references import ref_kwargs, type_key_for
from .shortener_models import DownloadShortLink, FirmwareShortenerOverride
from .tracking_models import FirmwareDownloadAttempt, FirmwareStats, FirmwareView


def purge_firmware_references(firmware: Any) -> dict[str, int]:
    """Remove every live row that refers to a deleted firmware; returns the counts.

    Tokens (with their sessions and gate logs), views, download attempts and daily
    stats, the served-download library rows and the shortener rows go. A pending
    access-change request is withdrawn (the file it asked about is gone); the
    append-only access history (:class:`AccessChangeLog`), shortener audit outcomes
    and external-link reports stay on purpose, with the file name they carried.
    Running it again is a no-op.
    """
    ref = ref_kwargs(firmware)
    key = {"firmware_type": type_key_for(firmware), "firmware_id": firmware.pk}
    return {
        "tokens": DownloadToken.objects.filter(**ref).delete()[0],
        "attempts": FirmwareDownloadAttempt.objects.filter(**ref).delete()[0],
        "views": FirmwareView.objects.filter(**ref).delete()[0],
        "stats": FirmwareStats.objects.filter(**ref).delete()[0],
        "library": ServedDownload.objects.filter(**ref).delete()[0],
        "short_links": DownloadShortLink.objects.filter(**key).delete()[0],
        "overrides": FirmwareShortenerOverride.objects.filter(**key).delete()[0],
        "requests_withdrawn": AccessChangeRequest.objects.filter(
            **ref, status=AccessChangeRequest.Status.PENDING
        ).update(status=AccessChangeRequest.Status.WITHDRAWN),
    }
