from __future__ import annotations

from django.contrib.syndication.views import Feed
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.feedgenerator import Atom1Feed, Rss201rev2Feed

from .models import Post, PostStatus


def _posts():
    return (
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=timezone.now())
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-published_at")[:50]
    )


class LatestRssFeed(Feed):
    title = "Latest Posts"
    link = "/feed/rss/"
    description = "RSS feed for recent posts"
    feed_type = Rss201rev2Feed

    def items(self):
        return _posts()

    def item_title(self, item: Post):
        return item.title

    def item_description(self, item: Post):
        return item.summary or item.body[:240]

    def item_link(self, item: Post):
        return reverse("blog:post_detail", kwargs={"slug": item.slug})

    def item_pubdate(self, item: Post):
        return item.published_at


class LatestAtomFeed(LatestRssFeed):
    feed_type = Atom1Feed
    subtitle = LatestRssFeed.description
    link = "/feed/atom/"


def json_feed(request: HttpRequest):
    posts = _posts()
    items = [
        {
            "id": p.slug,
            "url": reverse("blog:post_detail", kwargs={"slug": p.slug}),
            "title": p.title,
            "summary": p.summary,
            "content_html": p.body,
            "date_published": p.published_at.isoformat() if p.published_at else None,
            "author": {"name": str(p.author)},
            "tags": list(p.tags.values_list("name", flat=True)),
        }
        for p in posts
    ]
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Latest Posts",
        "home_page_url": reverse("blog:post_list"),
        "feed_url": "/feed/json/",
        "items": items,
    }
    return JsonResponse(feed)
