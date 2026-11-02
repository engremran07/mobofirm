from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from apps.seo.models_settings import SeoAutomationSettings
from apps.site_settings.models import SiteSettings

from .models import Tag


class PublishedTagsSitemap(Sitemap):
    """
    Optional sitemap for tag landing pages (public).
    """

    changefreq = "weekly"
    priority = 0.3

    def __init__(self):
        super().__init__()
        try:
            ss = SiteSettings.get_solo()
        except Exception:
            ss = None
        try:
            seo = SeoAutomationSettings.get_solo()
            self._enabled = bool(getattr(seo, "tag_sitemap_enabled", True))
        except Exception:
            self._enabled = True
        force_https = bool(getattr(ss, "force_https", False))
        self.protocol = "https" if force_https else "http"

    def items(self):
        if not self._enabled:
            return []
        return Tag.objects.filter(is_active=True).order_by("-usage_count")[:500]

    def lastmod(self, obj: Tag):
        return obj.updated_at or timezone.now()

    def location(self, obj: Tag):
        try:
            return reverse("tags:detail", kwargs={"slug": obj.slug})
        except Exception:
            return ""
