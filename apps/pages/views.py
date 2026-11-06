from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.decorators import require_app_enabled

from . import widgets
from .models import Page

TOC_MIN_ENTRIES = 3
RELATED_PAGE_LIMIT = 6


@require_GET
@require_app_enabled("pages")
def page_index(request: HttpRequest) -> HttpResponse:
    """List the pages the current user may read, with public content widgets."""
    pages = Page.objects.visible_to(request.user).only(
        "slug", "title", "seo_description", "content", "content_format", "updated_at"
    )
    return render(
        request,
        "pages/index.html",
        {
            "pages": pages,
            "latest_firmwares": widgets.latest_firmwares(limit=6),
            "latest_posts": widgets.latest_posts(limit=4),
            "latest_tools": widgets.latest_tools(limit=6),
        },
    )


@require_GET
@require_app_enabled("pages")
def page_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render one CMS page; hidden pages return 404 so their existence is not leaked."""
    visible = Page.objects.visible_to(request.user)
    page = visible.filter(slug=slug).first()
    if page is None:
        raise Http404
    rendered = page.rendered
    return render(
        request,
        "pages/page.html",
        {
            "page": page,
            "rendered": rendered,
            "show_toc": len(rendered.toc) >= TOC_MIN_ENTRIES,
            "related_pages": visible.exclude(pk=page.pk).only("slug", "title")[
                :RELATED_PAGE_LIMIT
            ],
            "latest_posts": widgets.latest_posts(limit=3),
        },
    )
