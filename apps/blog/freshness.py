"""Content freshness for editors: which published articles have gone stale.

An article is stale when it is live and its last update (``Post.updated_at``, which
the workflow, the editor and the automation bump only on a real change; counters use
``QuerySet.update`` and never touch it) is older than ``BlogSettings.stale_after_days``.
The signal is for staff only; readers see the plain "Updated" date.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import DatabaseError
from django.db.models import QuerySet
from django.utils import timezone

from .models import BlogSettings, Post, PostStatus

DEFAULT_STALE_DAYS = 180


def stale_after_days() -> int:
    """The configured threshold in days; the default when settings are unreadable."""
    try:
        return BlogSettings.get_solo().stale_after_days
    except DatabaseError:
        return DEFAULT_STALE_DAYS


def stale_cutoff(now: datetime | None = None, days: int | None = None) -> datetime:
    """Posts last updated before this moment are stale."""
    moment = now or timezone.now()
    return moment - timedelta(days=stale_after_days() if days is None else days)


def stale_posts(queryset: QuerySet[Post] | None = None) -> QuerySet[Post]:
    """Live posts not updated since the cutoff (oldest update first)."""
    posts = Post.objects.all() if queryset is None else queryset
    return posts.filter(
        status=PostStatus.PUBLISHED,
        publish_at__lte=timezone.now(),
        updated_at__lt=stale_cutoff(),
    ).order_by("updated_at")


def stale_post_count() -> int:
    """Number of stale live posts (the admin dashboard figure)."""
    return stale_posts().count()


def days_since_update(post: Post, now: datetime | None = None) -> int:
    """Whole days since the post was last updated (never negative)."""
    return max(((now or timezone.now()) - post.updated_at).days, 0)


def is_stale(post: Post, now: datetime | None = None) -> bool:
    """Whether ``post`` is live and past the staleness threshold."""
    moment = now or timezone.now()
    return post.is_live and post.updated_at < stale_cutoff(moment)
