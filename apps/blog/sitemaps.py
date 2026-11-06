from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from apps.site_settings.services import get_site_settings

from .models import Post


class PublishedBlogPostsSitemap(Sitemap):
    """Live, indexable posts; a custom canonical elsewhere removes the post."""

    changefreq = "weekly"
    priority = 0.6

    def __init__(self):
        super().__init__()
        self.protocol = "https" if get_site_settings().force_https else "http"

    def items(self):
        return Post.objects.live().filter(noindex=False).order_by("-publish_at")

    def lastmod(self, obj: Post):
        return obj.updated_at or obj.publish_at or timezone.now()

    def location(self, obj: Post):
        return reverse("blog:post_detail", kwargs={"slug": obj.slug})
