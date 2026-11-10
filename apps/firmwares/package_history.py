"""Package-period download history: purge of old periods and backfill of old grants.

Files served under a package are tied to that package period (``ServedDownload``
with ``access_rule="package"`` and ``user_package_id``). When the user activates or
renews a package a new period starts; the history of the previous period is then
purged once the shop's retention (default: immediately) has passed. Free files and
files bought one by one are never touched.

The shop is an optional seam (``apps.shop.download_rules``): without it nothing is
purged or tagged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from apps.core.events.bus import event_bus

from .access import RULES_MODULE, optional_module
from .models import AccessTier, DownloadRule, ServedDownload

logger = logging.getLogger(__name__)

PACKAGE_ACTIVATED_EVENT = "shop.package_activated"


def purge_superseded_package_history(*, user_id: int | None = None) -> int:
    """Delete the history of package periods that a newer period has replaced.

    Returns the number of purged rows. ``user_id`` limits the purge to one account
    (used right after an activation); without it every account is swept.
    """
    rules = optional_module(RULES_MODULE)
    if rules is None:
        return 0
    rows = ServedDownload.objects.filter(
        access_rule=DownloadRule.PACKAGE, user_package_id__isnull=False
    )
    if user_id is not None:
        rows = rows.filter(user_id=user_id)
    periods = set(rows.values_list("user_package_id", flat=True).distinct())
    expired = rules.superseded_periods(periods)
    if not expired:
        return 0
    deleted, _ = rows.filter(user_package_id__in=expired).delete()
    logger.info("Purged %s package-period download records", deleted)
    return int(deleted)


def reset_free_allowance(row: ServedDownload) -> bool:
    """Start a free file's re-download window and count again (staff exception).

    Returns ``False`` for rows without a free allowance (package and lifetime files).
    """
    if row.access_rule != DownloadRule.FREE:
        return False
    now = timezone.now()
    ServedDownload.objects.filter(pk=row.pk).update(
        served_at=now, last_served_at=now, times_served=1
    )
    return True


def on_package_activated(data: Any) -> None:
    """Event handler: a package was activated, so its predecessor's history goes."""
    if isinstance(data, dict) and data.get("user_id") is not None:
        purge_superseded_package_history(user_id=int(data["user_id"]))


def connect() -> None:
    """Subscribe to the shop's activation event once (``FirmwaresConfig.ready``)."""
    event_bus.unsubscribe(PACKAGE_ACTIVATED_EVENT, on_package_activated)
    event_bus.subscribe(PACKAGE_ACTIVATED_EVENT, on_package_activated)


@dataclass
class BackfillResult:
    """What :func:`backfill_download_rules` changed."""

    lifetime: int = 0
    package: int = 0
    untagged: int = 0
    consumption_periods: int = 0

    @property
    def changed(self) -> int:
        """Total rows changed."""
        return self.lifetime + self.package + self.consumption_periods


def _bought_files(rules: Any) -> set[tuple[int, int, Any]]:
    """(user id, content type id, file id) of files bought one by one."""
    return set(rules.purchased_files())


def _featured_rows() -> QuerySet[ServedDownload]:
    """Featured rows still to classify (not yet lifetime or period-tagged)."""
    return (
        ServedDownload.objects.filter(tier=AccessTier.FEATURED)
        .exclude(access_rule=DownloadRule.LIFETIME)
        .exclude(access_rule=DownloadRule.PACKAGE, user_package_id__isnull=False)
    )


def backfill_download_rules(*, dry_run: bool = False) -> BackfillResult:
    """Give grants made before the lifetime rules the right rule (idempotent).

    Paid files become lifetime; featured files bought with credits become lifetime;
    other featured files become package files tagged with the period that covered the
    day they were served. Free files keep the default rule. Running it again changes
    nothing that is already right.
    """
    result = BackfillResult()
    paid = ServedDownload.objects.filter(tier=AccessTier.PAID).exclude(
        access_rule=DownloadRule.LIFETIME
    )
    result.lifetime += paid.count()
    if not dry_run:
        paid.update(access_rule=DownloadRule.LIFETIME, user_package_id=None)
    rules = optional_module(RULES_MODULE)
    if rules is None:
        return result
    if not dry_run:
        result.consumption_periods = rules.backfill_consumption_periods()
    bought = _bought_files(rules)
    for row in _featured_rows().iterator():
        if (row.user_id, row.content_type_id, row.object_id) in bought:
            result.lifetime += 1
            if not dry_run:
                ServedDownload.objects.filter(pk=row.pk).update(
                    access_rule=DownloadRule.LIFETIME, user_package_id=None
                )
            continue
        period = rules.period_at(row.user_id, row.served_at)
        result.package += 1
        if period is None:
            result.untagged += 1
        if not dry_run:
            ServedDownload.objects.filter(pk=row.pk).update(
                access_rule=DownloadRule.PACKAGE, user_package_id=period
            )
    return result
