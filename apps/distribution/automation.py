"""Background automation: evergreen re-shares and retention housekeeping."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db.models import Max, Sum
from django.utils import timezone

from apps.core.app_service import app_enabled
from apps.distribution import content as content_module
from apps.distribution import media, services
from apps.distribution.constants import (
    HOUSEKEEPING_EVERY_SECONDS,
    SHAREABLE_KINDS,
)
from apps.distribution.models import (
    Channel,
    ClickEvent,
    DistributionSettings,
    ShareJob,
    ShareLog,
    ShortLink,
)

logger = logging.getLogger(__name__)
MIN_EVERGREEN_CLICKS = 3
CANDIDATE_POOL = 60
DEAD_STATUSES = (ShareJob.Status.CANCELLED, ShareJob.Status.REJECTED)


def _candidates() -> list[tuple[str, str]]:
    """Content ranked by real clicks across every channel (best first)."""
    rows = (
        ShortLink.objects.filter(content_kind__in=SHAREABLE_KINDS)
        .values("content_kind", "object_ref")
        .annotate(total=Sum("clicks"))
        .filter(total__gte=MIN_EVERGREEN_CLICKS)
        .order_by("-total")[:CANDIDATE_POOL]
    )
    return [(row["content_kind"], row["object_ref"]) for row in rows]


def run_evergreen(now: datetime | None = None) -> int:
    """Re-share top performers on channels whose preset allows it.

    Spaced (``evergreen_gap_days`` per item per channel), capped
    (``evergreen_per_week`` per channel), never a duplicate, and only content that is
    still public. Each re-share is a normal idempotent job (``...:r<round>``).
    """
    moment = now or timezone.now()
    if not app_enabled("distribution"):
        return 0
    created = 0
    pool: list[tuple[str, str]] | None = None
    channels = Channel.objects.filter(
        enabled=True, campaign__evergreen=True
    ).select_related("campaign")
    for channel in channels:
        preset = channel.campaign
        if preset is None or channel.configuration_problem:
            continue
        week = (
            ShareJob.objects.filter(
                channel=channel,
                reshare_round__gt=0,
                created_at__gte=moment - timedelta(days=7),
            )
            .exclude(status__in=DEAD_STATUSES)
            .count()
        )
        budget = preset.evergreen_per_week - week
        if budget <= 0:
            continue
        pool = _candidates() if pool is None else pool
        gap_start = moment - timedelta(days=preset.evergreen_gap_days)
        for kind, ref in pool:
            if budget <= 0:
                break
            history = ShareJob.objects.filter(
                channel=channel, content_kind=kind, object_ref=ref
            ).exclude(status__in=DEAD_STATUSES)
            if history.filter(created_at__gte=gap_start).exists():
                continue
            item = content_module.load(kind, ref)
            if item is None:
                continue
            rounds = history.aggregate(top=Max("reshare_round"))["top"]
            if rounds is None:
                continue  # only items this channel has already shared are re-shared
            jobs = services.enqueue(
                item, now=moment, reshare_round=rounds + 1, only=channel
            )
            created += len(jobs)
            budget -= len(jobs)
    return created


def purge_clicks(days: int, now: datetime | None = None) -> int:
    if days <= 0:
        return 0
    cutoff = (now or timezone.now()) - timedelta(days=days)
    return ClickEvent.objects.filter(bucket__lt=cutoff).delete()[0]


def purge_logs(days: int, now: datetime | None = None) -> int:
    if days <= 0:
        return 0
    cutoff = (now or timezone.now()) - timedelta(days=days)
    return ShareLog.objects.filter(created_at__lt=cutoff).delete()[0]


def housekeeping(now: datetime | None = None, *, force: bool = False) -> dict[str, int]:
    """Retention purge + evergreen scheduling; at most once an hour unless forced."""
    moment = now or timezone.now()
    cfg = DistributionSettings.get_solo()
    last = cfg.last_housekeeping_at
    if (
        not force
        and last is not None
        and moment - last < timedelta(seconds=HOUSEKEEPING_EVERY_SECONDS)
    ):
        return {}
    result = {
        "clicks": purge_clicks(cfg.click_retention_days, moment),
        "logs": purge_logs(cfg.log_retention_days, moment),
        "media": media.purge_expired(cfg.media_retention_days, moment),
        "evergreen": run_evergreen(moment),
    }
    cfg.last_housekeeping_at = moment
    cfg.save(update_fields=["last_housekeeping_at"])
    logger.info("distribution housekeeping: %s", result)
    return result
