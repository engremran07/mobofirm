"""Indexation policy shared by the resolver, robots.txt, sitemaps and the crawler."""

from __future__ import annotations

import html
import re
from urllib.parse import urlencode

from django.http import HttpRequest
from django.utils.html import strip_tags

# Crawlers are told not to fetch these at all (no value, or private by design).
ROBOTS_DISALLOW = (
    "/admin/",
    "/accounts/",
    "/users/",
    "/devices/",
    "/comments/",
    "/ads/",
    "/storage/",
    "/files/",
    "/contributions/",
    "/shop/orders/",
    "/pricing/orders/",
    "/seo/",
    "/health/",
    "/firmwares/serve/",
    "/firmwares/api/",
    "/blog/api/",
)

TRACKING_PARAMS = frozenset(
    {"gclid", "fbclid", "msclkid", "yclid", "ref", "mc_cid", "mc_eid"}
)

SEARCH_ENGINE_ROBOTS = "max-image-preview:large, max-snippet:-1, max-video-preview:-1"

_WHITESPACE = re.compile(r"\s+")

AI_SEARCH_BOTS = ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot")
AI_USER_AGENTS = (
    "ChatGPT-User",
    "Claude-User",
    "Perplexity-User",
    "Meta-ExternalFetcher",
)
AI_TRAINING_BOTS = (
    "GPTBot",
    "ClaudeBot",
    "anthropic-ai",
    "Google-Extended",
    "Applebot-Extended",
    "CCBot",
    "Bytespider",
    "Meta-ExternalAgent",
    "Amazonbot",
)

# Search engine crawlers that get an explicit group. Each vendor documents these
# tokens; Googlebot-Image falls back to Googlebot only when it has no group of its own.
SEARCH_BOTS = ("Googlebot", "Googlebot-Image", "bingbot")
# Crawlers that honour ``Crawl-delay`` (Google documents that it ignores it).
CRAWL_DELAY_BOTS = ("bingbot",)

# Paths that stay fetchable for every crawler: rendering needs the CSS and JS bundles
# and images are discovered through Googlebot-Image.
ROBOTS_ALLOW = ("/static/", "/media/")

# Query parameters that only create duplicate or session-specific URLs. They are
# blocked with RFC 9309 wildcards (``?`` first parameter, ``&`` any later one).
ROBOTS_PARAM_TRAPS = (
    "sort",
    "order",
    "sessionid",
    "session_id",
    "sid",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "msclkid",
)

# ``?page=N`` is not trapped here: RFC 9309 patterns cannot express a numeric limit,
# so deep pagination stays crawlable and the resolver governs it (noindex, canonical).


def is_private_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ROBOTS_DISALLOW)


# Pages that have no content object (the home page, listings, hubs, feeds index) keep
# their SEO override in ``seo.Metadata`` under this reference, keyed by the URL path.
ROUTE_LABEL = "seo.seosettings"
ROUTE_PREFIX = "route:"


def route_ref(path: str) -> tuple[str, str]:
    """Override reference of a route-level page (no content object)."""
    return ROUTE_LABEL, f"{ROUTE_PREFIX}{path.split('?', 1)[0] or '/'}"


def is_route_ref(label: str, pk: str) -> bool:
    return label == ROUTE_LABEL and pk.startswith(ROUTE_PREFIX)


_NOINDEX_PATHS = re.compile(r"^/firmwares/file/[^/]+/[^/]+/download/")


def is_noindex_path(path: str) -> bool:
    """Responses that must carry ``X-Robots-Tag: noindex`` whatever their body."""
    return is_private_path(path) or bool(_NOINDEX_PATHS.match(path))


def robots_directive(*, index: bool, follow: bool) -> str:
    follow_token = "follow" if follow else "nofollow"
    if index:
        return f"index, {follow_token}, {SEARCH_ENGINE_ROBOTS}"
    return f"noindex, {follow_token}"


def clean_text(value: str) -> str:
    """Plain text from stored content: strip tags, decode entities, fold spaces."""
    return _WHITESPACE.sub(" ", html.unescape(strip_tags(value or ""))).strip()


def truncate(value: str, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut or text[: limit - 1]}…"


def compose_title(page_title: str, site_name: str, separator: str) -> str:
    base = clean_text(page_title)
    if not base:
        return site_name
    full = f"{base}{separator}{site_name}"
    if len(full) <= 70 or not site_name:
        return full
    return truncate(base, 90)


def canonical_query(
    request: HttpRequest, allowed: tuple[str, ...]
) -> tuple[str, list[str]]:
    """Canonical query string and the parameters that make the URL a variant.

    ``page`` stays only above page 1; ``allowed`` parameters identify distinct
    content; tracking parameters are ignored; anything else marks a filtered or
    parameterised variant that must not be indexed.
    """
    keep: dict[str, str] = {}
    variants: list[str] = []
    for key in sorted(request.GET):
        value = request.GET.get(key, "")
        if key in allowed and value:
            keep[key] = value
        elif key == "page":
            if value.isdigit() and int(value) > 1:
                keep[key] = value
            elif not value.isdigit() and value:
                variants.append(key)
        elif key.startswith("utm_") or key in TRACKING_PARAMS:
            continue
        elif value:
            variants.append(key)
    return urlencode(sorted(keep.items())), variants
