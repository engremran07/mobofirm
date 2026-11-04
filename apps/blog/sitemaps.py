from __future__ import annotations

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone

from .models import Post, PostStatus


class PublishedBlogPostsSitemap(Sitemap):
    """
    Enterprise sitemap for published blog posts.
    - respects publish_at
    - honors per-post canonical_url when set
    - uses Django's FORCE_HTTPS setting for protocol selection
    """

    def __init__(self):
        super().__init__()
        self.protocol = "https" if settings.FORCE_HTTPS else "http"

    def items(self):
        now = timezone.now()
        items = (
            Post.objects.filter(
                status=PostStatus.PUBLISHED, publish_at__lte=now, noindex=False
            )
            .order_by("-publish_at")
            .select_related("author", "category")
        )
        # Apply locale translations if available (best-effort, requires request in context)
        return items

    def lastmod(self, obj: Post):
        return obj.updated_at or obj.publish_at

    def location(self, obj: Post):
        if obj.canonical_url:
            return obj.canonical_url
        return reverse("blog:post_detail", kwargs={"slug": obj.slug})

    def changefreq(self, obj: Post):
        return "weekly"

    def priority(self, obj: Post):
        # Feature/high-value posts could be prioritized later; default to 0.6
        return 0.6
