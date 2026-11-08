from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils.functional import cached_property

from .models import Page


class PublishedPagesSitemap(Sitemap):
    """Public, published CMS pages that opted into the sitemap."""

    def items(self):
        return (
            Page.objects.visible_to(None)
            .filter(include_in_sitemap=True)
            .order_by("-updated_at")
        )

    def lastmod(self, obj: Page):
        return obj.updated_at

    def location(self, item: Page) -> str:
        return item.get_absolute_url()

    def changefreq(self, obj: Page) -> str:
        return obj.changefreq

    def priority(self, obj: Page) -> float:
        return float(obj.priority)


class StaticViewsSitemap(Sitemap):
    """Fixed public entry points that are not backed by database rows."""

    changefreq = "weekly"
    priority = 0.4
    route_names = (
        "home",
        "blog:post_list",
        "firmwares:brand_list",
        "firmwares:browse",
        "firmwares:flashing_tools",
        "tags:list",
        "pages:index",
    )

    @cached_property
    def _locations(self) -> list[str]:
        return [reverse(name) for name in self.route_names]

    def items(self) -> list[str]:
        return self._locations

    def location(self, item: str) -> str:
        return item
