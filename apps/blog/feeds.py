from __future__ import annotations

from django.contrib.syndication.views import Feed
from django.http import Http404, HttpRequest, JsonResponse
from django.urls import reverse
from django.utils.feedgenerator import Atom1Feed, Rss201rev2Feed, SyndicationFeed

from apps.blog import selectors
from apps.blog.access import blog_enabled_for
from apps.blog.models import Post
from apps.blog.rendering import plain_excerpt, render_article

FEED_LIMIT = 50


def _posts():
    return selectors.live_posts().order_by("-published_at", "-pk")[:FEED_LIMIT]


def _curated_tag_names(post: Post) -> list[str]:
    return [tag.name for tag in post.tags.all() if selectors.is_public_tag(tag)]


class LatestRssFeed(Feed):
    title = "Mobofirm journal"
    description = "Firmware guides, device research and release notes."
    feed_type: type[SyndicationFeed] = Rss201rev2Feed

    def link(self):
        return reverse("blog:post_list")

    def feed_url(self):
        return reverse("blog:feed_rss")

    def items(self):
        return _posts()

    def item_title(self, item: Post):
        return item.title

    def item_description(self, item: Post):
        return item.summary or plain_excerpt(item.body, 240)

    def item_link(self, item: Post):
        return item.get_absolute_url()

    def item_pubdate(self, item: Post):
        return item.published_at

    def item_updateddate(self, item: Post):
        return item.updated_at

    def item_author_name(self, item: Post):
        return selectors.author_name(item)

    def item_categories(self, item: Post):
        return _curated_tag_names(item)


class LatestAtomFeed(LatestRssFeed):
    feed_type = Atom1Feed
    subtitle = LatestRssFeed.description

    def feed_url(self):
        return reverse("blog:feed_atom")


def json_feed(request: HttpRequest) -> JsonResponse:
    if not blog_enabled_for(request.user):
        raise Http404
    items = [
        {
            "id": post.slug,
            "url": request.build_absolute_uri(post.get_absolute_url()),
            "title": post.title,
            "summary": post.summary or plain_excerpt(post.body, 240),
            "content_html": str(render_article(post.body).html),
            "date_published": post.published_at.isoformat()
            if post.published_at
            else None,
            "author": {"name": selectors.author_name(post)},
            "tags": _curated_tag_names(post),
        }
        for post in _posts()
    ]
    return JsonResponse(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": LatestRssFeed.title,
            "home_page_url": request.build_absolute_uri(reverse("blog:post_list")),
            "feed_url": request.build_absolute_uri(reverse("blog:feed_json")),
            "items": items,
        }
    )
