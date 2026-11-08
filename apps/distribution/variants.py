"""A/B selection of the opening style, per channel, from real click data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Sum
from django.utils import timezone

from apps.distribution.models import Campaign, Channel, ShortLink

WINDOW_DAYS = 90
MIN_POSTS_PER_STYLE = 2
LETTERS = "ABCDEFGH"


@dataclass(frozen=True, slots=True)
class StyleStat:
    hook: str
    variant: str
    posts: int
    clicks: int

    @property
    def per_post(self) -> float:
        return self.clicks / self.posts if self.posts else 0.0


@dataclass(frozen=True, slots=True)
class AbResult:
    stats: tuple[StyleStat, ...]
    total_clicks: int
    winner: str  # '' until enough clicks

    @property
    def decided(self) -> bool:
        return bool(self.winner)


def style_stats(
    channel: Channel, preset: Campaign, now: datetime | None = None
) -> tuple[StyleStat, ...]:
    since = (now or timezone.now()) - timedelta(days=WINDOW_DAYS)
    rows: dict[str, dict[str, Any]] = {
        row["hook"]: dict(row)
        for row in ShortLink.objects.filter(
            channel=channel,
            created_at__gte=since,
            job__status__in=("queued", "pending", "needs_media", "sent"),
        )
        .values("hook")
        .annotate(posts=Count("id"), clicks=Sum("clicks"))
    }
    stats = []
    for index, hook in enumerate(preset.hooks):
        row: dict[str, Any] = rows.get(hook, {})
        stats.append(
            StyleStat(
                hook,
                LETTERS[index % len(LETTERS)],
                row.get("posts", 0),
                row.get("clicks") or 0,
            )
        )
    return tuple(stats)


def evaluate(
    channel: Channel, preset: Campaign, now: datetime | None = None
) -> AbResult:
    """The standing of each style and the winner (once ``ab_min_clicks`` clicks exist and
    every style has been posted at least twice)."""
    stats = style_stats(channel, preset, now)
    total = sum(s.clicks for s in stats)
    winner = ""
    if (
        preset.ab_test
        and len(stats) > 1
        and total >= preset.ab_min_clicks
        and all(s.posts >= MIN_POSTS_PER_STYLE for s in stats)
    ):
        winner = max(stats, key=lambda s: (s.per_post, -stats.index(s))).hook
    return AbResult(stats, total, winner)


def choose_hook(
    channel: Channel,
    preset: Campaign,
    *,
    allow: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """(hook, variant letter) for the next post on ``channel``.

    Without A/B testing the first style is used. With it, styles rotate (least used
    first) until the winner is decided; the winner is then always used.
    """
    result = evaluate(channel, preset, now)
    stats = [s for s in result.stats if allow is None or s.hook in allow] or list(
        result.stats
    )
    if not preset.ab_test:
        return stats[0].hook, stats[0].variant
    if result.winner:
        for stat in stats:
            if stat.hook == result.winner:
                return stat.hook, stat.variant
    pick = min(stats, key=lambda s: (s.posts, stats.index(s)))
    return pick.hook, pick.variant
