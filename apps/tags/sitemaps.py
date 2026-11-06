from __future__ import annotations

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from apps.site_settings.services import get_site_settings

from .models import Tag
from .selectors import public_tags, with_live_post_counts


class PublishedTagsSitemap(Sitemap):
    """
    Optional sitemap for tag landing pages (public).
    """

    changefreq = "weekly"
    priority = 0.3

    def __init__(self):
        super().__init__()
        self._enabled = bool(getattr(settings, "TAGS_SITEMAP_ENABLED", True))
        self.protocol = "https" if get_site_settings().force_https else "http"

    def items(self):
        if not self._enabled:
            return []
        return (
            with_live_post_counts(public_tags())
            .filter(live_count__gt=0)
            .order_by("-live_count", "name")[:500]
        )

    def lastmod(self, obj: Tag):
        return obj.updated_at

    def location(self, obj: Tag):
        return reverse("tags:detail", kwargs={"slug": obj.slug})
