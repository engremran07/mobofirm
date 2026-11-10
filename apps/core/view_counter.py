"""One cheap, bot-aware public view counter shared by every content page.

A view counts once per browser session per day. Crawlers, link-preview fetchers,
``HEAD`` requests and browser prefetch/prerender are never counted. Callers do
the atomic ``F()`` increment; this module only decides *whether* to count.
"""

from __future__ import annotations

import re
from datetime import date

from django.http import HttpRequest
from django.utils import timezone

SESSION_KEY = "content_views"
MAX_REMEMBERED = 300

BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|monitor|facebookexternalhit|embedly|"
    r"whatsapp|telegram|discord|slack|skype|vkshare|pinterest|linkedin|mastodon|"
    r"bluesky|headless|python-requests|curl/|wget|go-http-client|java/|okhttp|"
    r"libwww|httpclient|scrapy|axios|node-fetch|lighthouse|pingdom|uptime",
    re.IGNORECASE,
)


def is_bot_request(request: HttpRequest) -> bool:
    """True for crawlers, previews, ``HEAD`` and prefetch/prerender requests."""
    agent = request.headers.get("User-Agent", "")
    purpose = (
        request.headers.get("Sec-Purpose", "") + request.headers.get("Purpose", "")
    ).lower()
    return (
        request.method == "HEAD"
        or not agent
        or bool(BOT_PATTERN.search(agent))
        or "prefetch" in purpose
        or "prerender" in purpose
        or request.headers.get("X-Moz", "").lower() == "prefetch"
    )


def _today() -> date:
    return timezone.localdate()


def should_count(request: HttpRequest, kind: str, key: object) -> bool:
    """Record ``kind:key`` for today and say whether this view is a new, human one.

    Returns ``False`` for bots and for objects this session already viewed today.
    The remembered keys reset every day, so a returning reader counts again.
    """
    if is_bot_request(request):
        return False
    session = getattr(request, "session", None)
    if session is None:
        return False
    today = _today().isoformat()
    stored = session.get(SESSION_KEY)
    seen: list[str] = []
    if isinstance(stored, dict) and stored.get("day") == today:
        seen = [str(item) for item in stored.get("keys", [])]
    token = f"{kind}:{key}"
    if token in seen:
        return False
    session[SESSION_KEY] = {"day": today, "keys": [*seen, token][-MAX_REMEMBERED:]}
    return True
