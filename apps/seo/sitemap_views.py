"""Public sitemap, robots and well-known file endpoints.

Every endpoint is GET/HEAD only, emits validators (``ETag``, ``Last-Modified`` when a
real modification time exists), a short ``Cache-Control`` with
``stale-while-revalidate`` and answers conditional requests with 304. Sitemap
responses carry ``X-Robots-Tag: noindex`` (they are for crawlers, not for results).
"""

from __future__ import annotations

import gzip
import logging
from datetime import date, datetime

from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
)
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.decorators import require_app_enabled
from apps.site_settings.services import get_site_settings

from . import sitemap_build, wellknown
from .httpcache import CACHE_CONTROL, conditional
from .human_sitemap import build_human_sitemap
from .models import SEOSettings
from .robots import build_robots, sitemap_enabled
from .sitemap_registry import get_sitemaps
from .siteurls import site_origin

logger = logging.getLogger(__name__)

XML = "application/xml; charset=utf-8"
GZIP = "application/gzip"
TEXT = "text/plain; charset=utf-8"
SITEMAP_HEADERS = {"X-Robots-Tag": "noindex"}

safe = require_http_methods(["GET", "HEAD"])


@require_app_enabled("seo")
@safe
def human_sitemap(request: HttpRequest) -> HttpResponse:
    """Categorized, live sitemap for visitors, built from public route inventory."""
    return render(
        request,
        "core/site_map.html",
        {
            "site_name": get_site_settings().site_name,
            "sections": build_human_sitemap(request),
        },
    )


def _enabled_row() -> SEOSettings:
    row = SEOSettings.get_solo()
    if not sitemap_enabled(row):
        raise Http404
    return row


def _xml_response(
    request: HttpRequest,
    template: str,
    context: dict[str, object],
    *,
    gz: bool,
    last_modified: datetime | date | None,
) -> HttpResponse:
    body = render_to_string(template, context).encode()
    if len(body) > sitemap_build.MAX_BYTES:
        logger.error(
            "Sitemap %s is %d bytes, over the 50 MB protocol limit; lower the page size.",
            request.path,
            len(body),
        )
        return HttpResponse(
            "Sitemap exceeds the 50 MB limit; lower the sitemap page size.",
            status=500,
            content_type=TEXT,
        )
    if gz:
        return conditional(
            request,
            gzip.compress(body, mtime=0),
            content_type=GZIP,
            last_modified=last_modified,
            etag_salt="gz",
            extra_headers=SITEMAP_HEADERS,
        )
    return conditional(
        request,
        body,
        content_type=XML,
        last_modified=last_modified,
        extra_headers=SITEMAP_HEADERS,
    )


@safe
@require_app_enabled("seo")
def sitemap_index(request: HttpRequest, gz: str = "") -> HttpResponse:
    row = _enabled_row()
    origin = site_origin(request)
    rows: list[dict[str, str]] = []
    moments: list[datetime | date | None] = []
    for listed in sitemap_build.catalog(row, origin):
        moment = listed.latest_lastmod
        moments.append(moment)
        rows.append(
            {
                "loc": f"{origin}{listed.path}",
                "lastmod": sitemap_build.w3c(moment) if moment else "",
            }
        )
    news = sitemap_build.news_entries(row, origin)
    if news:
        newest = max(item.published for item in news)
        moments.append(newest)
        rows.append(
            {
                "loc": f"{origin}{reverse('seo_public:sitemap_news')}",
                "lastmod": sitemap_build.w3c(newest),
            }
        )
    return _xml_response(
        request,
        "seo/sitemap_index.xml",
        {"sitemaps": rows},
        gz=bool(gz),
        last_modified=sitemap_build.latest(moments),
    )


@safe
@require_app_enabled("seo")
def sitemap_file(
    request: HttpRequest, key: str, page: str, gz: str = ""
) -> HttpResponse:
    """One numbered file: ``/sitemap-<section>[-<partition>]-<page>.xml[.gz]``."""
    row = _enabled_row()
    number = int(page)
    located = sitemap_build.locate(key, get_sitemaps())
    if located is None:
        raise Http404("No sitemap section with that name.")
    section, partition = located
    origin = site_origin(request)
    wanted = next(
        (
            candidate
            for candidate in sitemap_build.section_files(section, row, origin)
            if candidate.partition == partition and candidate.page == number
        ),
        None,
    )
    if wanted is None:
        raise Http404("Sitemap page not found.")
    images = sitemap_build.attach_images(section, [wanted], origin, row)
    urls = [
        {
            "loc": entry.loc,
            "lastmod": sitemap_build.w3c(entry.lastmod) if entry.lastmod else "",
            "images": images.get(entry.loc, []),
            "alternates": entry.alternates,
        }
        for entry in wanted.entries
    ]
    return _xml_response(
        request,
        "seo/sitemap.xml",
        {"urlset": urls},
        gz=bool(gz),
        last_modified=wanted.latest_lastmod,
    )


@safe
@require_app_enabled("seo")
def sitemap_legacy(request: HttpRequest, section: str) -> HttpResponse:
    """``/sitemap-<section>.xml[?p=N]`` moved permanently to the path-based file."""
    _enabled_row()
    if section not in get_sitemaps():
        raise Http404("No sitemap section with that name.")
    if sitemap_build.is_partitioned(section):
        return HttpResponsePermanentRedirect(reverse("seo_public:sitemap"))
    page = request.GET.get("p", "1")
    if not page.isdigit() or int(page) < 1:
        raise Http404("Sitemap page not found.")
    return HttpResponsePermanentRedirect(f"/sitemap-{section}-{int(page)}.xml")


@safe
@require_app_enabled("seo")
def sitemap_news(request: HttpRequest, gz: str = "") -> HttpResponse:
    """Google News sitemap: only when enabled, only fresh qualifying posts."""
    row = _enabled_row()
    if not row.sitemap_news_enabled:
        raise Http404
    news = sitemap_build.news_entries(row, site_origin(request))
    if not news:
        raise Http404
    publication = row.site_name or get_site_settings().site_name
    items = [
        {
            "loc": item.loc,
            "title": item.title,
            "published": sitemap_build.w3c(item.published),
            "language": item.language,
        }
        for item in news
    ]
    return _xml_response(
        request,
        "seo/sitemap_news.xml",
        {"news": items, "publication": publication},
        gz=bool(gz),
        last_modified=max(item.published for item in news),
    )


@safe
def robots_txt(request: HttpRequest) -> HttpResponse:
    return conditional(
        request,
        build_robots(request).encode(),
        content_type=TEXT,
        cache_control=CACHE_CONTROL,
    )


def _text(request: HttpRequest, body: str | None) -> HttpResponse:
    if body is None:
        raise Http404
    return conditional(
        request, body.encode(), content_type=TEXT, cache_control=CACHE_CONTROL
    )


@safe
def security_txt(request: HttpRequest) -> HttpResponse:
    row = SEOSettings.get_solo()
    return _text(request, wellknown.security_txt(row, site_origin(request)))


@safe
def humans_txt(request: HttpRequest) -> HttpResponse:
    return _text(request, wellknown.humans_txt(SEOSettings.get_solo()))


@safe
def ads_txt(request: HttpRequest) -> HttpResponse:
    return _text(request, wellknown.ads_txt("ads.txt"))


@safe
def app_ads_txt(request: HttpRequest) -> HttpResponse:
    return _text(request, wellknown.ads_txt("app-ads.txt"))


@safe
def indexnow_key(request: HttpRequest, key: str) -> HttpResponse:
    """Serve the IndexNow ownership key at ``/<key>.txt`` when it is enabled."""
    row = SEOSettings.get_solo()
    if not (row.seo_enabled and row.indexnow_enabled and row.indexnow_key == key):
        raise Http404
    return HttpResponse(key, content_type="text/plain; charset=utf-8")
