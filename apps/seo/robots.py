"""robots.txt generation from the single settings authority."""

from __future__ import annotations

from django.http import HttpRequest
from django.urls import reverse

from .models import SEOSettings
from .policy import (
    AI_SEARCH_BOTS,
    AI_TRAINING_BOTS,
    AI_USER_AGENTS,
    ROBOTS_DISALLOW,
)
from .siteurls import site_origin


def sitemap_enabled(row: SEOSettings) -> bool:
    return row.seo_enabled and row.sitemap_enabled


def build_robots(request: HttpRequest) -> str:
    """Private areas are blocked for everyone; AI crawler groups follow settings.

    Noindex (not Disallow) is used for thin or parameterised pages so crawlers can
    still read the directive; Disallow is reserved for areas with no search value.
    """
    row = SEOSettings.get_solo()
    lines = ["User-agent: *", *(f"Disallow: {path}" for path in ROBOTS_DISALLOW), ""]
    blocked: list[str] = []
    if not row.allow_ai_search_bots:
        blocked.extend(AI_SEARCH_BOTS)
    if not row.allow_ai_user_agents:
        blocked.extend(AI_USER_AGENTS)
    if not row.allow_ai_training_bots:
        blocked.extend(AI_TRAINING_BOTS)
    if blocked:
        lines.extend(f"User-agent: {agent}" for agent in blocked)
        lines.extend(["Disallow: /", ""])
    if sitemap_enabled(row):
        lines.append(f"Sitemap: {site_origin(request)}{reverse('seo_public:sitemap')}")
    return "\n".join(lines).rstrip("\n") + "\n"
