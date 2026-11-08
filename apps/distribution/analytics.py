"""Read-only analytics over short-link clicks and share jobs (server-rendered)."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractHour, ExtractWeekDay
from django.utils import timezone

from apps.distribution import media, variants
from apps.distribution.constants import CHANNEL_TRIP_AFTER
from apps.distribution.models import (
    Campaign,
    Channel,
    ClickEvent,
    ShareJob,
    ShortLink,
)

WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def dashboard_stats(now: datetime | None = None) -> dict[str, Any]:
    moment = now or timezone.now()
    since = moment - timedelta(days=7)
    clicks = dict(
        ClickEvent.objects.filter(bucket__gte=since)
        .values_list("link__channel")
        .annotate(total=Count("id"))
    )
    rows = Channel.objects.select_related("campaign").annotate(
        sent=Count("jobs", filter=Q(jobs__status="sent", jobs__created_at__gte=since)),
        failed=Count(
            "jobs", filter=Q(jobs__status="failed", jobs__created_at__gte=since)
        ),
        queued=Count("jobs", filter=Q(jobs__status__in=["queued", "needs_media"])),
        pending=Count("jobs", filter=Q(jobs__status="pending")),
        skipped=Count(
            "jobs", filter=Q(jobs__status="skipped", jobs__created_at__gte=since)
        ),
    )
    channel_rows = [
        {"channel": channel, "clicks": clicks.get(channel.pk, 0)} for channel in rows
    ]
    failures = ShareJob.objects.filter(status="failed").select_related("channel")[:10]
    return {
        "channels": channel_rows,
        "recent_failures": list(failures),
        "trip_after": CHANNEL_TRIP_AFTER,
        "queue_depth": ShareJob.objects.filter(
            status__in=["queued", "needs_media"]
        ).count(),
        "awaiting_approval": ShareJob.objects.filter(status="pending").count(),
        "sent_7d": ShareJob.objects.filter(status="sent", sent_at__gte=since).count(),
        "failed_7d": ShareJob.objects.filter(
            status="failed", created_at__gte=since
        ).count(),
        "clicks_7d": sum(clicks.values()),
        "top_content": top_content(30, 5, moment),
        "capabilities": media.capabilities(),
    }


def top_content(
    days: int, limit: int, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Most-clicked shared content over the window, with a readable title."""
    since = (now or timezone.now()) - timedelta(days=days)
    groups = (
        ClickEvent.objects.filter(bucket__gte=since)
        .values("link__content_kind", "link__object_ref")
        .annotate(clicks=Count("id"))
        .order_by("-clicks")[:limit]
    )
    result = []
    for group in groups:
        job = (
            ShareJob.objects.filter(
                content_kind=group["link__content_kind"],
                object_ref=group["link__object_ref"],
            )
            .order_by("created_at")
            .first()
        )
        result.append(
            {
                "kind": group["link__content_kind"],
                "ref": group["link__object_ref"],
                "title": job.title if job else group["link__object_ref"],
                "clicks": group["clicks"],
                "shares": ShortLink.objects.filter(
                    content_kind=group["link__content_kind"],
                    object_ref=group["link__object_ref"],
                ).count(),
            }
        )
    return result


def per_channel(days: int, now: datetime | None = None) -> list[dict[str, Any]]:
    since = (now or timezone.now()) - timedelta(days=days)
    clicks = dict(
        ClickEvent.objects.filter(bucket__gte=since)
        .values_list("link__channel")
        .annotate(total=Count("id"))
    )
    rows = []
    for channel in Channel.objects.all():
        posts = ShareJob.objects.filter(
            channel=channel, status="sent", sent_at__gte=since
        ).count()
        total = clicks.get(channel.pk, 0)
        rows.append(
            {
                "channel": channel,
                "posts": posts,
                "clicks": total,
                "per_post": round(total / posts, 2) if posts else 0,
            }
        )
    return sorted(rows, key=lambda row: -row["clicks"])


def per_variant(now: datetime | None = None) -> list[dict[str, Any]]:
    """A/B standing per channel: clicks per post for each opening style."""
    result = []
    for channel in Channel.objects.select_related("campaign"):
        preset = channel.campaign or Campaign.default()
        outcome = variants.evaluate(channel, preset, now)
        if outcome.total_clicks or any(s.posts for s in outcome.stats):
            result.append({"channel": channel, "result": outcome, "preset": preset})
    return result


def heatmap(days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Clicks by weekday x hour (server time zone), for the best-time table."""
    since = (now or timezone.now()) - timedelta(days=days)
    rows = (
        ClickEvent.objects.filter(bucket__gte=since)
        .annotate(day=ExtractWeekDay("bucket"), hour=ExtractHour("bucket"))
        .values("day", "hour")
        .annotate(total=Count("id"))
    )
    grid = [[0] * 24 for _ in range(7)]
    for row in rows:
        grid[(row["day"] - 1) % 7][row["hour"]] = row["total"]
    peak = max((max(line) for line in grid), default=0)
    table = [
        {
            "label": WEEKDAYS[index],
            "cells": [
                {"hour": hour, "clicks": value, "level": _level(value, peak)}
                for hour, value in enumerate(line)
            ],
        }
        for index, line in enumerate(grid)
    ]
    best = max(
        ((d, h) for d in range(7) for h in range(24) if grid[d][h]),
        key=lambda pair: grid[pair[0]][pair[1]],
        default=None,
    )
    return {
        "rows": table,
        "peak": peak,
        "best": f"{WEEKDAYS[best[0]]} {best[1]:02d}:00" if best else "",
    }


def _level(value: int, peak: int) -> int:
    """0-4 intensity for the cell colour (never colour-only: the number is shown)."""
    if not value or not peak:
        return 0
    return min(4, 1 + int(3 * value / peak))


def total_clicks(days: int = 30, now: datetime | None = None) -> int:
    since = (now or timezone.now()) - timedelta(days=days)
    return ClickEvent.objects.filter(bucket__gte=since).aggregate(n=Count("id"))["n"]


def lifetime_clicks() -> int:
    return ShortLink.objects.aggregate(n=Sum("clicks"))["n"] or 0


def _csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    text = str(value)
    return f"'{text}" if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


class _Echo:
    def write(self, value: str) -> str:
        return value


def csv_rows() -> Iterator[str]:
    """One CSV line per short link (streamed)."""
    writer = csv.writer(_Echo())
    yield writer.writerow(
        [
            "code",
            "channel",
            "platform",
            "content_kind",
            "content_ref",
            "hook",
            "posted_at",
            "clicks",
            "bot_clicks",
            "status",
        ]
    )
    links = ShortLink.objects.select_related("channel", "job").order_by("-created_at")
    for link in links.iterator(chunk_size=500):
        yield writer.writerow(
            [
                _csv_safe(value)
                for value in (
                    link.code,
                    link.channel.name,
                    link.channel.kind,
                    link.content_kind,
                    link.object_ref,
                    link.hook,
                    link.job.sent_at.isoformat() if link.job.sent_at else "",
                    link.clicks,
                    link.bot_clicks,
                    link.job.status,
                )
            ]
        )
