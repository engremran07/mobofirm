"""Public feed endpoints under ``/feeds/``."""

from __future__ import annotations

import gzip
from datetime import timedelta

from django.core.cache import cache
from django.db.models import F
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import http_date
from django.views.decorators.http import require_safe

from apps.core.app_service import app_enabled
from apps.core.internal_request import is_internal_crawl
from apps.distribution import feeds
from apps.distribution.models import DistributionSettings, FeedConfig, FeedStat

CACHE_SECONDS = 300
GZIP_MIN_BYTES = 1024
ENABLED_CACHE_KEY = "distribution:feeds_enabled"


def feeds_enabled() -> bool:
    """Public switch, cached briefly so the head link costs no query per page."""
    cached = cache.get(ENABLED_CACHE_KEY)
    if cached is None:
        cached = bool(DistributionSettings.get_solo().feeds_enabled)
        cache.set(ENABLED_CACHE_KEY, cached, 60)
    return bool(cached)


def feed_enabled(key: str) -> bool:
    row = FeedConfig.objects.filter(key=key).values_list("enabled", flat=True).first()
    return row is not False


def _record(
    key: str, *, not_modified: bool, size: int, count: int, built: bool
) -> None:
    """Aggregate counters only; the in-process SEO crawler is never counted."""
    now = timezone.now()
    config, _created = FeedConfig.objects.get_or_create(key=key)
    updates: dict[str, object] = {
        "last_fetched_at": now,
        "fetch_count": F("fetch_count") + 1,
    }
    if not_modified:
        updates["not_modified_count"] = F("not_modified_count") + 1
    if built:
        updates.update(last_built_at=now, last_size=size, last_item_count=count)
    FeedConfig.objects.filter(pk=config.pk).update(**updates)
    bucket = now.replace(minute=0, second=0, microsecond=0)
    stat, _created = FeedStat.objects.get_or_create(feed_key=key, bucket=bucket)
    FeedStat.objects.filter(pk=stat.pk).update(
        fetches=F("fetches") + 1,
        not_modified=F("not_modified") + (1 if not_modified else 0),
    )


def _headers(response: HttpResponse, etag: str, modified_http: str) -> HttpResponse:
    response["ETag"] = etag
    response["Last-Modified"] = modified_http
    response["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    response["Vary"] = "Accept-Encoding"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
def feed(request: HttpRequest, key: str, fmt: str) -> HttpResponse:
    if not app_enabled("distribution") or fmt not in feeds.FORMATS:
        raise Http404
    spec = feeds.spec_for(key)
    cfg = DistributionSettings.get_solo()
    if spec is None or not cfg.feeds_enabled or not feed_enabled(key):
        raise Http404
    dynamic = spec.scope in {"brand", "chipset", "tag"}
    if dynamic and not cfg.feeds_dynamic_enabled:
        raise Http404
    if spec.scope == "news" and not feeds.news_available():
        raise Http404
    built = feeds.build(spec, fmt, cfg)
    if dynamic and built.count == 0:
        raise Http404
    modified_http = http_date(built.modified.timestamp())
    counted = not is_internal_crawl(request)
    if feeds.not_modified(
        built.etag,
        built.modified,
        request.headers.get("If-None-Match", ""),
        request.headers.get("If-Modified-Since", ""),
    ):
        if counted:
            _record(key, not_modified=True, size=0, count=built.count, built=False)
        return _headers(HttpResponse(status=304), built.etag, modified_http)
    body = built.body
    response = HttpResponse(content_type=f"{built.content_type}; charset=utf-8")
    if (
        "gzip" in request.headers.get("Accept-Encoding", "")
        and len(body) >= GZIP_MIN_BYTES
    ):
        body = gzip.compress(body, mtime=0)
        response["Content-Encoding"] = "gzip"
    response.content = body
    if counted:
        _record(
            key, not_modified=False, size=len(built.body), count=built.count, built=True
        )
    return _headers(response, built.etag, modified_http)


def public_feeds() -> list[dict[str, object]]:
    """The feeds a visitor may subscribe to (fixed ones that are on and available)."""
    off = set(FeedConfig.objects.filter(enabled=False).values_list("key", flat=True))
    rows: list[dict[str, object]] = []
    for key, title, description in feeds.FIXED:
        if key in off or (key == feeds.NEWS_KEY and not feeds.news_available()):
            continue
        rows.append(
            {
                "key": key,
                "title": title,
                "description": description,
                "links": [
                    {"fmt": fmt, "label": label, "url": feeds.feed_url(key, fmt)}
                    for fmt, (_mime, label) in feeds.FORMATS.items()
                ],
            }
        )
    return rows


@require_safe
def index(request: HttpRequest) -> HttpResponse:
    cfg = DistributionSettings.get_solo()
    if not app_enabled("distribution") or not cfg.feeds_enabled:
        raise Http404
    return render(
        request,
        "distribution/feeds_index.html",
        {
            "feeds": public_feeds(),
            "hub": cfg.websub_hub_url,
            "dynamic": cfg.feeds_dynamic_enabled,
            "window": timedelta(hours=48),
        },
    )
