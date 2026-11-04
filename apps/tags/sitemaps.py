from __future__ import annotations

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from .models import Tag


class PublishedTagsSitemap(Sitemap):
    """
    Optional sitemap for tag landing pages (public).
    """

    changefreq = "weekly"
    priority = 0.3

    def __init__(self):
        super().__init__()
        self._enabled = bool(getattr(settings, "TAGS_SITEMAP_ENABLED", True))
        self.protocol = "https" if settings.FORCE_HTTPS else "http"

    def items(self):
        if not self._enabled:
            return []
        return Tag.objects.filter(
            is_active=True, is_curated=True, is_deleted=False
        ).order_by("-usage_count")[:500]

    def lastmod(self, obj: Tag):
        return obj.updated_at or timezone.now()

    def location(self, obj: Tag):
        try:
            return reverse("tags:detail", kwargs={"slug": obj.slug})
        except Exception:
            return ""
