"""Template tags for public content statistics: views, downloads and freshness."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django import template
from django.utils import timezone
from django.utils.timesince import timesince

register = template.Library()

THOUSAND = 1000
MILLION = 1_000_000


def short_number(value: int) -> str:
    """Compact form of ``value``: ``950``, ``1.2k``, ``12k``, ``3.4M``."""
    value = max(int(value or 0), 0)
    if value < THOUSAND:
        return str(value)
    divisor, suffix = (MILLION, "M") if value >= MILLION else (THOUSAND, "k")
    scaled = value / divisor
    text = f"{scaled:.1f}" if scaled < 10 else f"{scaled:.0f}"
    return f"{text.removesuffix('.0')}{suffix}"


def updated_ago(moment: datetime | None) -> str:
    """'3 days ago' (largest unit only), or 'just now' under a minute."""
    if moment is None:
        return ""
    if timezone.now() - moment < timedelta(minutes=1):
        return "just now"
    return f"{timesince(moment, depth=1)} ago"


def _first_int(obj: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, int):
            return value
    return None


def _updated_at(obj: Any) -> datetime | None:
    for name in ("content_updated_at", "updated_at", "published_at"):
        value = getattr(obj, name, None)
        if isinstance(value, datetime):
            return value
    return None


@register.filter
def short_count(value: object) -> str:
    """Filter form of :func:`short_number` for templates."""
    if isinstance(value, (int, float, str)):
        try:
            return short_number(int(value))
        except ValueError:
            return "0"
    return "0"


@register.inclusion_tag("includes/content_stats.html")
def content_stats(
    obj: Any, *, compact: bool = False, reading: bool = True
) -> dict[str, Any]:
    """Views, downloads (firmware) and last-updated line for a post or firmware.

    ``reading=False`` omits the reading time for surfaces that already show it.
    """
    views = _first_int(obj, "view_count", "views_count")
    downloads = _first_int(obj, "download_count")
    reading_time = _first_int(obj, "reading_time") if reading else None
    updated = _updated_at(obj)
    return {
        "views": views,
        "views_short": short_number(views or 0),
        "downloads": downloads,
        "downloads_short": short_number(downloads or 0),
        "reading_time": reading_time or None,
        "updated": updated,
        "updated_ago": updated_ago(updated),
        "compact": compact,
    }
