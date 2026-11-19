"""Transfer leases: the cap on concurrent downloads measures real transfers.

A package sells "N downloads at once". Counting statuses did not measure that (the
only completion write happened before the first byte left the server, and an external
host's session stayed ``started`` for half an hour). A :class:`DownloadSession` is now
a *lease* on one transfer slot:

* admitted when the download token is consumed (``open_lease``): the slot is held for
  the link's own TTL while the browser opens the signed link;
* extended to a size-aware transfer window when the link is first opened
  (``redeem_link``), which is also the only moment the file is charged to the user;
* given back when the transfer is reported finished (``report_transfer``: the Django
  streaming path reports exactly, a reverse proxy's completion log feed may call the
  same function) or, whatever happens, when ``lease_expires_at`` passes. The expiry
  predicate is evaluated at count time, so a lost completion signal frees the slot
  without any worker; ``release_expired`` only tidies the rows.

External hosts are a hand-off: Mobofirm never sees their transfer, so no slot is held.

Every state change is one conditional ``UPDATE`` (compare and swap), so a duplicate
delivery or two parallel redemptions cannot both win.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.db.models import F, Q
from django.utils import timezone

from .models import DownloadSession
from .tracking_models import FirmwareDownloadAttempt

logger = logging.getLogger(__name__)

# A transfer is assumed to run at least this fast when sizing its window (256 KiB/s).
MIN_TRANSFER_RATE = 256 * 1024
MIN_TRANSFER_WINDOW = timedelta(minutes=10)
MAX_TRANSFER_WINDOW = timedelta(hours=6)
# How often one admitted link may be opened (first request plus resumes and ranges).
REDEMPTION_BUDGET = 5
# Rows written before the lease columns existed have no expiry; they hold a slot only
# for this long after they started.
LEGACY_SESSION_WINDOW = timedelta(minutes=30)


def transfer_window(size_bytes: int | None) -> timedelta:
    """How long a stored-file transfer may hold its slot: size aware, bounded."""
    if not size_bytes or size_bytes <= 0:
        return MIN_TRANSFER_WINDOW
    needed = timedelta(seconds=size_bytes / MIN_TRANSFER_RATE)
    return min(max(needed, MIN_TRANSFER_WINDOW), MAX_TRANSFER_WINDOW)


def held_filter(now: datetime) -> Q:
    """Sessions that hold a slot at ``now`` (unreleased and inside their lease)."""
    legacy = Q(
        lease_expires_at__isnull=True,
        status=DownloadSession.Status.STARTED,
        started_at__gte=now - LEGACY_SESSION_WINDOW,
    )
    return Q(released_at__isnull=True) & (Q(lease_expires_at__gt=now) | legacy)


def held_transfers(user_pk: Any, *, now: datetime | None = None) -> int:
    """Transfers the user holds a slot for right now."""
    now = now or timezone.now()
    return DownloadSession.objects.filter(held_filter(now), user_id=user_pk).count()


def open_lease(
    session: DownloadSession, *, link_ttl: timedelta, external: bool
) -> DownloadSession:
    """Start the lease of a freshly admitted session (or hand it off at once).

    Stored files hold the slot for the link's TTL while the browser opens it; an
    external host never holds one.
    """
    now = timezone.now()
    if external:
        session.status = DownloadSession.Status.COMPLETED
        session.completed_at = now
        session.released_at = now
        session.lease_expires_at = None
        fields = ["status", "completed_at", "released_at", "lease_expires_at"]
    else:
        session.lease_expires_at = now + link_ttl
        fields = ["lease_expires_at"]
    session.save(update_fields=fields)
    return session


@dataclass(frozen=True)
class Redemption:
    """Result of opening a signed link: ``first`` is the one that may charge."""

    first: bool


def redeem_link(
    session: DownloadSession, size_bytes: int | None, *, now: datetime | None = None
) -> Redemption | None:
    """Open the session's link once more; ``None`` when it may not be opened.

    The first open marks the file as served and widens the lease to the transfer
    window; later opens (resume, ranged parts) only spend the redemption budget. A
    released, expired or exhausted session refuses, which makes a replayed link dead.
    """
    now = now or timezone.now()
    live = DownloadSession.objects.filter(
        pk=session.pk, released_at__isnull=True, lease_expires_at__gt=now
    )
    first = live.filter(redeem_count=0).update(
        redeem_count=1,
        redeemed_at=now,
        status=DownloadSession.Status.COMPLETED,
        completed_at=now,
        lease_expires_at=now + transfer_window(size_bytes),
    )
    if first:
        return Redemption(first=True)
    again = live.filter(redeem_count__gte=1, redeem_count__lt=REDEMPTION_BUDGET).update(
        redeem_count=F("redeem_count") + 1
    )
    return Redemption(first=False) if again else None


def fail_session(session: DownloadSession, reason: str) -> None:
    """Give the slot back after a refusal that happened once the session existed."""
    now = timezone.now()
    DownloadSession.objects.filter(pk=session.pk, released_at__isnull=True).update(
        status=DownloadSession.Status.FAILED,
        error=reason[:500],
        released_at=now,
        completed_at=None,
    )
    FirmwareDownloadAttempt.objects.filter(session_id=session.pk).exclude(
        status__in=("completed", "failed")
    ).update(status="failed", failed_at=now, failure_reason=reason[:500])


def report_transfer(
    session_id: int, *, bytes_delivered: int, completed: bool, error: str = ""
) -> bool:
    """Record how a transfer ended and free its slot (idempotent; first report wins).

    ``completed=False`` marks an interrupted transfer (the user's client went away).
    A completion that arrives after the lease already expired still records the
    bytes, because the expiry never knew the outcome. Returns whether anything
    changed, so a duplicate delivery is a harmless ``False``.
    """
    now = timezone.now()
    bytes_delivered = max(int(bytes_delivered), 0)
    note = "" if completed else (error or "Transfer interrupted")[:500]
    status = (
        DownloadSession.Status.COMPLETED
        if completed
        else DownloadSession.Status.ABORTED
    )
    updated = DownloadSession.objects.filter(
        pk=session_id, redeem_count__gt=0, released_at__isnull=True
    ).update(
        status=status,
        bytes_delivered=bytes_delivered,
        error=note,
        released_at=now,
        completed_at=now if completed else None,
    )
    if not updated and completed:
        updated = DownloadSession.objects.filter(
            pk=session_id, redeem_count__gt=0, bytes_delivered=0
        ).update(
            status=DownloadSession.Status.COMPLETED,
            bytes_delivered=bytes_delivered,
            error="",
            completed_at=now,
        )
    if updated:
        attempts = FirmwareDownloadAttempt.objects.filter(session_id=session_id)
        if completed:
            attempts.update(
                status="completed", completed_at=now, bytes_transferred=bytes_delivered
            )
        else:
            attempts.update(
                status="failed",
                failed_at=now,
                failure_reason=note,
                bytes_transferred=bytes_delivered,
            )
    return bool(updated)


def release_expired(now: datetime | None = None) -> int:
    """Tidy leases that ran out. Returns how many slots were released.

    A link that was never opened ends ``aborted``; a transfer whose lease ran out
    without a report keeps its ``completed`` status (served) and zero reported bytes.
    """
    now = now or timezone.now()
    expired = DownloadSession.objects.filter(
        released_at__isnull=True, lease_expires_at__lte=now
    )
    never_opened_ids = list(
        expired.filter(redeem_count=0).values_list("pk", flat=True)[:1000]
    )
    if never_opened_ids:
        DownloadSession.objects.filter(
            pk__in=never_opened_ids, redeem_count=0, released_at__isnull=True
        ).update(
            status=DownloadSession.Status.ABORTED,
            error="The download link was never opened.",
            released_at=now,
        )
        FirmwareDownloadAttempt.objects.filter(
            session_id__in=never_opened_ids, status="ready"
        ).update(status="cancelled")
    opened = expired.filter(redeem_count__gt=0).update(released_at=now)
    return len(never_opened_ids) + opened


class MeteredFile(io.BufferedReader):
    """A file object that reports how many bytes were really read when it closes.

    Django's ``FileResponse`` reads it block by block and closes it when the response
    ends or the client disconnects, so the lease is released with the true outcome.
    """

    def __init__(self, path: Path, session_id: int, size_bytes: int) -> None:
        super().__init__(io.FileIO(path, "rb"))
        self._session_id = session_id
        self._expected = size_bytes
        self._sent = 0
        self._reported = False

    def read(self, size: int | None = -1, /) -> bytes:
        data = super().read(size)
        self._sent += len(data)
        return data

    def __del__(self) -> None:
        # A finalizer must never touch the database: a file that is dropped without
        # being closed simply lets its lease expire.
        self._reported = True
        self.close()

    def close(self) -> None:
        if not self.closed and not self._reported:
            self._reported = True
            report_transfer(
                self._session_id,
                bytes_delivered=self._sent,
                completed=self._sent >= self._expected,
                error=f"Client stopped after {self._sent} of {self._expected} bytes",
            )
        super().close()
