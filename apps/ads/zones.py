"""The fixed vocabulary of ad zones that the shared public layouts declare.

Templates never name individual placements. They call ``{% ad_zone "<code>" %}`` with one
of the codes below; the placement manager (data, not template source) decides which
zones are enabled for which template and which campaign or creative fills them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SIZE_PATTERN = re.compile(r"^\d{2,4}x\d{2,4}$")

# Banner sizes the stylesheet reserves space for (no layout shift when a creative loads).
RESERVED_SIZES: frozenset[str] = frozenset(
    {
        "300x250",
        "336x280",
        "300x600",
        "160x600",
        "728x90",
        "970x90",
        "970x250",
        "320x100",
        "320x50",
    }
)


@dataclass(frozen=True, slots=True)
class Zone:
    code: str
    label: str
    description: str
    default_size: str
    # Page-wide zones sit outside any container and bring their own.
    contained: bool = False


ZONES: tuple[Zone, ...] = (
    Zone(
        "header",
        "Header banner",
        "Leaderboard under the site navigation, above the page content.",
        "728x90",
        contained=True,
    ),
    Zone(
        "sidebar",
        "Sidebar",
        "Rectangle in a page sidebar or beside the main column.",
        "300x250",
    ),
    Zone(
        "in_content",
        "In content",
        "Inside an article or detail page, between sections of the content.",
        "300x250",
    ),
    Zone(
        "after_content",
        "After content",
        "Banner after the main content, before the footer.",
        "728x90",
        contained=True,
    ),
    Zone(
        "between_items",
        "Between list items",
        "Card between the items of a list or grid.",
        "300x250",
    ),
    Zone(
        "footer",
        "Footer banner",
        "Leaderboard directly above the site footer.",
        "728x90",
        contained=True,
    ),
)

ZONE_BY_CODE: dict[str, Zone] = {zone.code: zone for zone in ZONES}
ZONE_CHOICES: list[tuple[str, str]] = [(zone.code, zone.label) for zone in ZONES]

# Scanner suggestion suffix -> the zone that can actually render it.
SUGGESTION_ZONES: dict[str, str] = {
    "in-article-top": "in_content",
    "in-article-mid": "in_content",
    "content-top": "header",
    "content-bottom": "after_content",
    "sidebar-top": "sidebar",
    "sidebar-sticky": "sidebar",
    "in-feed": "between_items",
    "in-grid": "between_items",
    "above-footer": "footer",
    "after-hero": "header",
}

DEVICE_ALL = "all"
DEVICE_DESKTOP = "desktop"
DEVICE_MOBILE = "mobile"
DEVICE_CHOICES: list[tuple[str, str]] = [
    (DEVICE_ALL, "All devices"),
    (DEVICE_DESKTOP, "Desktop and tablet only"),
    (DEVICE_MOBILE, "Mobile only"),
]


def zone_for_suggestion(code: str) -> str:
    """Map a scanner suggestion (``<template>-<suffix>``) to a canonical zone, or ''."""
    for suffix, zone in SUGGESTION_ZONES.items():
        if code.endswith(suffix):
            return zone
    return ""


def reserved_size(allowed_sizes: str, zone_code: str) -> str:
    """The size to reserve: the first reservable size of the placement, else the zone's."""
    for raw in (allowed_sizes or "").split(","):
        size = raw.strip().lower()
        if size in RESERVED_SIZES:
            return size
    zone = ZONE_BY_CODE.get(zone_code)
    return zone.default_size if zone else "300x250"
