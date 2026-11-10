"""Download entitlement lifetime: how long a file served to a user stays fetchable.

Contract read by the firmware serving gate (``apps.firmwares.access``) through the
optional module seam; nothing here imports firmware models.

Three rules decide what a served file keeps:

* **free** files keep a bounded re-download allowance after the first serve
  (:func:`redownload_policy`: a window in days and a count, 0 = unlimited);
* **package** files (featured files served under a package) stay fetchable while the
  package period they were served under is the user's live one
  (:func:`period_is_live`); a renewal or new activation starts a new period and the
  old period's history is purged once :func:`superseded_periods` reports it;
* **lifetime** files (bought individually, with credits or by order) never expire.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone

from .entitlements import effective_entitlements
from .models import QuotaConsumption, ShopSettings, UserPackage


@dataclass(frozen=True)
class RedownloadPolicy:
    """Re-download allowance of a free file; 0 means unlimited for that dimension."""

    window_days: int
    max_redownloads: int

    @property
    def unlimited(self) -> bool:
        """Whether neither a time window nor a count limits re-downloads."""
        return self.window_days == 0 and self.max_redownloads == 0

    def describe(self) -> str:
        """Human text of the allowance, e.g. ``30 days / 5 re-downloads``."""
        parts = []
        if self.window_days:
            parts.append(f"{self.window_days} days")
        if self.max_redownloads:
            noun = "re-download" if self.max_redownloads == 1 else "re-downloads"
            parts.append(f"{self.max_redownloads} {noun}")
        return " / ".join(parts) or "unlimited"


def redownload_policy() -> RedownloadPolicy:
    """The admin-configured free re-download allowance."""
    settings = ShopSettings.get_solo()
    return RedownloadPolicy(
        window_days=settings.free_redownload_window_days,
        max_redownloads=settings.free_redownload_max,
    )


def current_period_id(user: Any) -> int | None:
    """Pk of the live package period of ``user`` (``None`` = no live package)."""
    package = effective_entitlements(user).package
    return package.pk if package is not None else None


def period_is_live(user: Any, period_id: int | None) -> bool:
    """Whether files served under package period ``period_id`` are still fetchable.

    Only the user's current package period is live: expiry, cancellation and a
    renewal (a new period) all end the previous one. A file served without a
    package period (access granted by a campaign) lives while featured access does.
    """
    if period_id is None:
        return bool(effective_entitlements(user).can_access_featured)
    return current_period_id(user) == period_id


def retention_days() -> int:
    """Days an old package period's history is kept after the next period began."""
    return ShopSettings.get_solo().package_history_retention_days


def superseded_periods(
    period_ids: Iterable[int], *, retention: int | None = None
) -> set[int]:
    """The given package periods whose history may now be purged.

    A period qualifies once a later package period of the same user started at least
    ``retention`` days ago (default: the admin setting, 0 = at activation).
    """
    ids = {pk for pk in period_ids if pk is not None}
    if not ids:
        return set()
    days = retention_days() if retention is None else retention
    cutoff = timezone.now() - timedelta(days=days)
    successor = (
        UserPackage.objects.filter(
            user_id=OuterRef("user_id"),
            is_baseline=False,
            started_at__gt=OuterRef("started_at"),
        )
        .order_by("started_at")
        .values("started_at")[:1]
    )
    return set(
        UserPackage.objects.filter(pk__in=ids)
        .annotate(next_start=Subquery(successor))
        .filter(next_start__lte=cutoff)
        .values_list("pk", flat=True)
    )


def purchased_files() -> list[tuple[int, int, Any]]:
    """(user id, content type id, file id) of featured files bought one by one."""
    return list(
        QuotaConsumption.objects.filter(
            reason=QuotaConsumption.Reason.PURCHASED
        ).values_list("user_id", "content_type_id", "object_id")
    )


def period_at(user_id: int, moment: datetime) -> int | None:
    """The package period that covered ``moment`` for the user, if any."""
    held = (
        UserPackage.objects.filter(
            user_id=user_id, is_baseline=False, started_at__lte=moment
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=moment))
        .order_by("-started_at")
        .first()
    )
    return held.pk if held is not None else None


def backfill_consumption_periods() -> int:
    """Tag featured quota rows counted before periods existed (idempotent).

    Each is tagged with the package period that covered its date; rows no period
    covers stay untagged and are counted again when next served.
    """
    tagged = 0
    rows = QuotaConsumption.objects.filter(
        reason=QuotaConsumption.Reason.FEATURED, user_package__isnull=True
    )
    for row in rows.iterator():
        period = period_at(row.user_id, row.consumed_at)
        if period is not None:
            QuotaConsumption.objects.filter(pk=row.pk).update(user_package_id=period)
            tagged += 1
    return tagged
