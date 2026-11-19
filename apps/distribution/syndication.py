"""Push side of syndication: tell hubs and search engines that a feed or page changed.

* **WebSub** (formerly PubSubHubbub): ``hub.mode=publish`` for every feed that gained the
  entry, to the hub configured in the distribution settings. Empty hub = nothing is
  queued and the feeds carry no ``rel=hub``. Hubs then push the new entry to subscribed
  readers; this is how feed readers (including Android apps) learn about it quickly.
* **IndexNow** through the existing ``apps.seo.indexnow`` seam (Bing, Yandex, ...), only
  when SEO has it enabled. Google does not take part in IndexNow.

Transaction boundary: the publish handlers call :func:`announce` after the content
transaction committed; it writes one ``PingOutbox`` row per (kind, target, entry) and never
touches the network. :func:`process_due` (the scheduler, every minute) delivers rows with
the same backoff as shares, ends in ``failed`` after ``MAX_ATTEMPTS``, and audits every
decision in ``ShareLog``. A replayed event finds the unique idempotency key and queues
nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.apps import apps
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.distribution import feeds
from apps.distribution.constants import LEASE_SECONDS, RETRY_BACKOFF
from apps.distribution.content import Content
from apps.distribution.models import DistributionSettings, PingOutbox, ShareLog
from apps.seo import indexnow
from apps.seo.fetcher import (
    BlockedDestination,
    FetchError,
    SafeFetcher,
    default_fetcher,
)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
RETRYABLE = frozenset({408, 425, 429})
OK_STATUSES = frozenset({200, 202, 204})
FORMATS = ("rss", "atom", "json")


def hub_url() -> str:
    """The configured hub when it is a usable https address, else ''."""
    value = DistributionSettings.get_solo().websub_hub_url.strip()
    parts = urlsplit(value)
    return value if parts.scheme == "https" and parts.hostname else ""


def indexnow_active() -> bool:
    if not apps.is_installed("apps.seo"):
        return False
    row = apps.get_model("seo", "SEOSettings").get_solo()
    return bool(row.seo_enabled and row.indexnow_enabled and row.indexnow_key)


def feed_keys_for(item: Content) -> list[str]:
    """Every public feed that gains ``item`` (the Google News feed only when active)."""
    keys = [feeds.SITE_KEY]
    keys.append(feeds.BLOG_KEY if item.kind == "blog" else feeds.FIRMWARE_KEY)
    if item.kind == "blog":
        keys.extend(f"tag/{slug}" for t in item.tags if (slug := slugify(t)))
        if feeds.news_available():
            keys.append(feeds.NEWS_KEY)
    else:
        for scope, value in (("brand", item.brand), ("chipset", item.chipset)):
            slug = slugify(value)
            if slug:
                keys.append(f"{scope}/{slug}")
    return list(dict.fromkeys(k for k in keys if feeds.spec_for(k) is not None))


def _log(message: str, *, level: str = "info", code: str = "") -> None:
    ShareLog.objects.create(level=level, message=message[:400], response_code=code[:20])


def _queue(kind: str, url: str, entry_ref: str, key: str) -> bool:
    try:
        with transaction.atomic():
            PingOutbox.objects.create(
                kind=kind,
                url=url[:500],
                entry_ref=entry_ref[:120],
                idempotency_key=key[:300],
            )
    except IntegrityError:
        return False
    return True


def announce(item: Content) -> int:
    """Queue the pings for a newly public ``item``; returns how many rows are new."""
    created = 0
    hub = hub_url()
    if hub:
        for key in feed_keys_for(item):
            for fmt in FORMATS:
                url = feeds.feed_url(key, fmt)
                created += _queue(
                    PingOutbox.Kind.WEBSUB,
                    url,
                    item.object_ref,
                    f"websub:{hub}:{url}:{item.object_ref}",
                )
    if indexnow_active():
        created += _queue(
            PingOutbox.Kind.INDEXNOW, item.url, item.object_ref, f"indexnow:{item.url}"
        )
    if created:
        _log(f"Queued {created} feed ping(s) for {item.object_ref}")
    return created


# ----------------------------------------------------------------- delivery ----


def _claim(pk: int, now: datetime) -> PingOutbox | None:
    with transaction.atomic():
        ping = (
            PingOutbox.objects.select_for_update()
            .filter(pk=pk, status=PingOutbox.Status.PENDING, next_attempt_at__lte=now)
            .first()
        )
        if ping is None:
            return None
        ping.attempts += 1
        ping.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
        ping.save(update_fields=["attempts", "next_attempt_at"])
        return ping


def _send_websub(ping: PingOutbox, fetcher: SafeFetcher) -> tuple[bool, bool, str, str]:
    """``(ok, retryable, code, detail)`` of one hub notification."""
    hub = hub_url()
    if not hub:
        return False, False, "config", "No WebSub hub is configured."
    body = urlencode({"hub.mode": "publish", "hub.url": ping.url}).encode()
    try:
        result = fetcher.fetch(
            hub,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
            follow_redirects=False,
            allowed_hosts=frozenset({urlsplit(hub).hostname or ""}),
        )
    except BlockedDestination as error:
        return False, False, "blocked", str(error)
    except FetchError as error:
        return False, True, error.code, str(error)
    if result.status in OK_STATUSES:
        return True, False, str(result.status), ""
    transient = result.status in RETRYABLE or result.status >= 500
    return (
        False,
        transient,
        str(result.status),
        f"The hub answered HTTP {result.status}.",
    )


def _send_indexnow(ping: PingOutbox) -> tuple[bool, bool, str, str]:
    try:
        result = indexnow.submit([ping.url])
    except indexnow.IndexNowError as error:
        return False, error.retryable, "indexnow", str(error)
    return True, False, str(result.status), ""


def deliver(
    pk: int, *, now: datetime | None = None, fetcher: SafeFetcher | None = None
) -> str:
    """One attempt: ``sent``, ``retried``, ``failed`` or ``''`` (not due)."""
    moment = now or timezone.now()
    ping = _claim(pk, moment)
    if ping is None:
        return ""
    if ping.kind == PingOutbox.Kind.WEBSUB:
        ok, retryable, code, detail = _send_websub(ping, fetcher or default_fetcher())
    else:
        ok, retryable, code, detail = _send_indexnow(ping)
    ping.response_code = code[:20]
    if ok:
        ping.status = PingOutbox.Status.SENT
        ping.sent_at = moment
        ping.last_error = ""
        outcome = "sent"
        _log(f"{ping.get_kind_display()} ping sent: {ping.url}", code=code)
    elif retryable and ping.attempts < MAX_ATTEMPTS:
        wait = RETRY_BACKOFF[min(ping.attempts, len(RETRY_BACKOFF)) - 1]
        ping.next_attempt_at = moment + timedelta(seconds=wait)
        ping.last_error = detail[:300]
        outcome = "retried"
        _log(
            f"{ping.get_kind_display()} ping attempt {ping.attempts} failed ({detail}); retry in {wait}s",
            level="warning",
            code=code,
        )
    else:
        ping.status = PingOutbox.Status.FAILED
        ping.last_error = detail[:300]
        outcome = "failed"
        _log(
            f"{ping.get_kind_display()} ping gave up after {ping.attempts} attempt(s): {detail}",
            level="error",
            code=code,
        )
    ping.save(
        update_fields=[
            "status",
            "sent_at",
            "last_error",
            "response_code",
            "next_attempt_at",
        ]
    )
    return outcome


def process_due(
    *, limit: int = 25, now: datetime | None = None, fetcher: SafeFetcher | None = None
) -> dict[str, int]:
    moment = now or timezone.now()
    counts = {"sent": 0, "retried": 0, "failed": 0}
    due = PingOutbox.objects.filter(
        status=PingOutbox.Status.PENDING, next_attempt_at__lte=moment
    ).order_by("next_attempt_at", "id")[:limit]
    for ping in list(due):
        result = deliver(ping.pk, now=moment, fetcher=fetcher)
        if result in counts:
            counts[result] += 1
    return counts


def retry(ping: PingOutbox) -> bool:
    if ping.status != PingOutbox.Status.FAILED:
        return False
    ping.status = PingOutbox.Status.PENDING
    ping.attempts = 0
    ping.next_attempt_at = timezone.now()
    ping.last_error = ""
    ping.save(update_fields=["status", "attempts", "next_attempt_at", "last_error"])
    _log(f"Ping retry requested: {ping.url}")
    return True


def cancel(ping: PingOutbox) -> bool:
    if ping.status != PingOutbox.Status.PENDING:
        return False
    ping.status = PingOutbox.Status.CANCELLED
    ping.save(update_fields=["status"])
    _log(f"Ping cancelled: {ping.url}")
    return True


def summary() -> dict[str, Any]:
    rows = PingOutbox.objects.all()
    return {
        "pending": rows.filter(status=PingOutbox.Status.PENDING).count(),
        "failed": rows.filter(status=PingOutbox.Status.FAILED).count(),
        "sent": rows.filter(status=PingOutbox.Status.SENT).count(),
    }
