"""SEO page providers for CMS pages (registered by ``apps.seo``)."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.urls import reverse

from apps.seo.contentsources import register_content_source
from apps.seo.contracts import Crumb, PageSEO
from apps.seo.models import SEOSettings
from apps.seo.policy import clean_text
from apps.seo.providers import ProviderContext, register_provider
from apps.seo.sitemap_registry import register_sitemap_filter

from .models import Page

HOME = ("Home", "/")


@register_provider("pages:index")
def index(ctx: ProviderContext) -> PageSEO:
    path = reverse("pages:index")
    return PageSEO(
        page_type="pages_index",
        title="Pages",
        description=ctx.describe("", "Information pages and policies."),
        path=path,
        crumbs=[Crumb(*HOME), Crumb("Pages", path)],
    )


@register_provider("pages:page")
def page_detail(ctx: ProviderContext) -> PageSEO | None:
    page = Page.objects.visible_to(None).filter(slug=ctx.kwargs.get("slug", "")).first()
    if page is None:
        return None
    path = page.get_absolute_url()
    return PageSEO(
        page_type="page",
        title=page.seo_title or page.title,
        description=ctx.describe(page.seo_description, clean_text(page.content)),
        path=path,
        canonical=page.canonical_url,
        image=page.og_image.url if page.og_image else "",
        crumbs=[Crumb(*HOME), Crumb(page.title, path)],
        object_ref=("pages.page", str(page.pk)),
        modified=page.updated_at,
        sources={"canonical": "object" if page.canonical_url else "route"},
    )


def _pages_filter(items: list, _row: SEOSettings) -> list:
    return [
        page
        for page in items
        if not page.canonical_url
        or urlsplit(page.canonical_url).path == page.get_absolute_url()
    ]


register_sitemap_filter("pages", _pages_filter)
register_content_source(
    "pages.page",
    url=lambda page: page.get_absolute_url(),
    is_public=lambda page: (
        page.is_published and page.access_level == Page.Access.PUBLIC
    ),
    notify_on_edit=True,
)
