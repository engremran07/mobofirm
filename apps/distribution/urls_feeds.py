"""``/feeds/``: the feed index and every feed (``<key>.<rss|atom|json>``)."""

from __future__ import annotations

from django.urls import path, re_path

from apps.distribution import feed_views

app_name = "feeds"

urlpatterns = [
    path("", feed_views.index, name="index"),
    re_path(
        r"^(?P<key>(?:section/|brand/|chipset/|tag/)?[a-z0-9][a-z0-9_-]*)\.(?P<fmt>rss|atom|json)$",
        feed_views.feed,
        name="feed",
    ),
]
