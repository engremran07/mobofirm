"""Celery tasks for tag automation. Every task is idempotent and safe to retry."""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import DatabaseError

from apps.tags.models import Tag

logger = logging.getLogger(__name__)

RETRYABLE = (DatabaseError,)


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE,
    retry_backoff=True,
    max_retries=3,
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def sync_post_tags(self, post_id: int) -> int:
    """Create, curate and attach tags for one post; returns how many tags apply."""
    from apps.blog.models import Post
    from apps.tags.services.automation import sync_post_tags as run

    post = (
        Post.objects.select_related("firmware_brand", "firmware_model", "category")
        .filter(pk=post_id)
        .first()
    )
    if post is None:
        logger.warning("Post %s vanished before tag sync", post_id)
        return 0
    return len(run(post))


@shared_task(
    bind=True,
    autoretry_for=RETRYABLE,
    retry_backoff=True,
    max_retries=3,
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def reattach_curated_tag(self, tag_id: int) -> int:
    """After a tag is approved, attach it to the live posts that mention it."""
    from apps.tags.services.automation import reattach_curated_tag as run

    tag = Tag.objects.filter(pk=tag_id, is_curated=True, is_active=True).first()
    if tag is None:
        return 0
    return run(tag)


@shared_task(acks_late=True, soft_time_limit=300, time_limit=600)
def curate_suggested_tags() -> int:
    """Promote suggestions that enough live posts mention, then attach them."""
    from apps.tags.services.automation import (
        curate_suggested_tags as run,
    )

    promoted = run()
    for tag_id in promoted:
        reattach_curated_tag.delay(tag_id)
    return len(promoted)


@shared_task(acks_late=True, soft_time_limit=300, time_limit=600)
def refresh_usage_counts() -> int:
    """Recompute every tag's live-post count (scheduled safety net)."""
    from apps.tags.services.automation import refresh_usage_counts as run

    return run()
