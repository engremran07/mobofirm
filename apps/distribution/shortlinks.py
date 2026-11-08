"""First-party short links: ``/go/<code>/`` redirects to the shared page and counts clicks.

Privacy: a counted click stores only the hour it happened. No IP, user agent, cookie or
referrer is kept, so the redirect needs no consent. Known bots, link-preview fetchers,
HEAD requests and browser prefetches are counted separately and never as clicks.

Safety: the destination is stored when the share is created (never taken from the request)
and is re-checked on every redirect to belong to this site, so the endpoint cannot be used
as an open redirect.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import transaction
from django.db.models import F
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.core.app_service import AppService
from apps.distribution.constants import SHORT_CODE_ALPHABET, SHORT_CODE_LENGTH
from apps.distribution.models import ClickEvent, ShortLink
from apps.seo.siteurls import absolute_url, same_site

BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|monitor|facebookexternalhit|embedly|"
    r"whatsapp|telegram|discord|slack|skype|vkshare|pinterest|linkedin|mastodon|"
    r"bluesky|headless|python-requests|curl/|wget|go-http-client|java/|okhttp|"
    r"libwww|httpclient|scrapy|axios|node-fetch|lighthouse|pingdom|uptime",
    re.IGNORECASE,
)


def new_code() -> str:
    return "".join(
        secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH)
    )


def short_url(code: str) -> str:
    return absolute_url(reverse("distribution:go", kwargs={"code": code}))


def referral_code(user: Any) -> str:
    """The user's referral code through the referral app's public contract (or '')."""
    if user is None:
        return ""
    lookup = AppService.export("referral", "referral_code_for")
    if lookup is None:
        return ""
    return str(lookup(user) or "")


def tracked_target(
    url: str,
    *,
    platform: str,
    campaign: str,
    hook: str,
    referral: str = "",
) -> str:
    """The page URL with UTM parameters (and the referral code when allowed)."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": platform,
            "utm_medium": "social",
            "utm_campaign": campaign or "share",
        }
    )
    if hook:
        query["utm_content"] = hook
    if referral:
        query["ref"] = referral
    return urlunsplit(parts._replace(query=urlencode(query)))


def is_bot(request: HttpRequest) -> bool:
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


def safe_target(url: str) -> str:
    """``url`` when it is an http(s) page on this site, else ''."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return url if same_site(url) else ""


def record_click(link: ShortLink, *, bot: bool, now: datetime | None = None) -> None:
    moment = now or timezone.now()
    if bot:
        ShortLink.objects.filter(pk=link.pk).update(bot_clicks=F("bot_clicks") + 1)
        return
    with transaction.atomic():
        ShortLink.objects.filter(pk=link.pk).update(
            clicks=F("clicks") + 1, last_click_at=moment
        )
        ClickEvent.objects.create(
            link=link, bucket=moment.replace(minute=0, second=0, microsecond=0)
        )
