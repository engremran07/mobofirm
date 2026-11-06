from __future__ import annotations

from django.contrib.sites.models import Site
from django.core.paginator import EmptyPage, PageNotAnInteger
from django.http import Http404, HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.core.decorators import require_app_enabled

from .models import SEOSettings
from .robots import build_robots, sitemap_enabled
from .sitemap_registry import get_sitemaps
from .siteurls import site_origin

XML = "application/xml"


def _enabled() -> bool:
    return sitemap_enabled(SEOSettings.get_solo())


@require_GET
@require_app_enabled("seo")
def sitemap_index(request: HttpRequest) -> HttpResponse:
    if not _enabled():
        raise Http404
    origin = site_origin(request)
    locations: list[dict[str, object]] = []
    for name, sitemap_class in get_sitemaps().items():
        sitemap = sitemap_class()
        paginator = sitemap.paginator
        if paginator.count == 0:
            continue
        section_url = f"{origin}" + reverse(
            "seo_public:sitemap_section", kwargs={"section": name}
        )
        last_mod = sitemap.get_latest_lastmod()
        locations.append({"location": section_url, "last_mod": last_mod})
        locations.extend(
            {"location": f"{section_url}?p={page}", "last_mod": last_mod}
            for page in range(2, paginator.num_pages + 1)
        )
    return TemplateResponse(
        request, "seo/sitemap_index.xml", {"sitemaps": locations}, content_type=XML
    )


@require_GET
@require_app_enabled("seo")
def sitemap_section(request: HttpRequest, section: str) -> HttpResponse:
    if not _enabled():
        raise Http404
    sitemap_class = get_sitemaps().get(section)
    if sitemap_class is None:
        raise Http404("No sitemap section with that name.")
    origin = site_origin(request)
    scheme, _, host = origin.partition("://")
    try:
        urls = sitemap_class().get_urls(
            page=request.GET.get("p", 1),
            site=Site(domain=host, name=host),
            protocol=scheme,
        )
    except (EmptyPage, PageNotAnInteger):
        raise Http404("Sitemap page not found.") from None
    return TemplateResponse(
        request, "seo/sitemap.xml", {"urlset": urls}, content_type=XML
    )


@require_GET
def robots_txt(request: HttpRequest) -> HttpResponse:
    return HttpResponse(build_robots(request), content_type="text/plain")


@require_GET
def indexnow_key(request: HttpRequest, key: str) -> HttpResponse:
    """Serve the IndexNow ownership key at ``/<key>.txt`` when it is enabled."""
    row = SEOSettings.get_solo()
    if not (row.seo_enabled and row.indexnow_enabled and row.indexnow_key == key):
        raise Http404
    return HttpResponse(key, content_type="text/plain")
