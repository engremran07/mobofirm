"""SEO page provider for the public feeds index (registered by ``apps.seo``)."""

from __future__ import annotations

from django.urls import reverse

from apps.seo.contracts import Crumb, PageSEO
from apps.seo.providers import ProviderContext, register_provider


@register_provider("feeds:index")
def feeds_index(ctx: ProviderContext) -> PageSEO:
    path = reverse("feeds:index")
    return PageSEO(
        page_type="feeds_index",
        title="RSS, Atom and JSON feeds",
        description=ctx.describe(
            "", "Follow new firmware and posts in any feed reader."
        ),
        path=path,
        crumbs=[Crumb("Home", "/"), Crumb("Feeds", path)],
    )
