from __future__ import annotations

import logging

from django.apps import apps
from django.db.models.signals import post_save

# NOTE: Cross-app imports are deferred inside handlers to prevent circular dependencies
# This module is loaded early during Django startup via AppConfig.ready()

logger = logging.getLogger(__name__)


def _sync_tag_usage(post) -> None:
    """
    Update tag usage_count for tags linked to post.
    OPTIMIZATION: Uses async task with bulk SQL operations instead of N queries.
    """
    try:
        tag_ids = list(post.tags.values_list("id", flat=True))
        if tag_ids:
            from apps.blog.tasks import sync_tag_usage_counts

            # Async task uses efficient bulk update
            sync_tag_usage_counts.delay(tag_ids)  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("Tag usage sync failed for post %s: %s", post.pk, e)


def _ensure_post_tags(post, post_status) -> None:
    if post.status != post_status.PUBLISHED or post.tags.exists():
        return
    from apps.tags.services import auto_tag_post

    auto_tag_post(post, allow_create=True, max_tags=5)


def _get_post_model():
    """Lazy import to prevent circular dependency."""
    from apps.blog.models import Post

    return Post


def _get_post_status():
    """Lazy import to prevent circular dependency."""
    from apps.blog.models import PostStatus

    return PostStatus


def connect_signals():
    """
    Connect blog signals. Called from AppConfig.ready().
    Uses lazy imports to prevent circular dependencies during app loading.
    """
    from django.conf import settings

    Post = _get_post_model()
    PostStatus = _get_post_status()

    def post_after_save(sender, instance, created=False, **kwargs):
        is_published = instance.status == PostStatus.PUBLISHED
        _ensure_post_tags(instance, PostStatus)
        _sync_tag_usage(instance)

        notifications_enabled = False
        if apps.is_installed("apps.users"):
            from apps.users.services.notifications import (
                notifications_enabled as is_enabled,
            )

            notifications_enabled = is_enabled()

        if created and is_published and notifications_enabled:
            try:
                from apps.blog.tasks import send_post_notifications_batched

                send_post_notifications_batched.delay(  # type: ignore[attr-defined]
                    post_id=instance.pk,
                    notification_title="New blog post",
                    notification_body=f"{instance.title} is now live.",
                    batch_size=500,
                )
                logger.info(f"Queued batched notification task for post {instance.pk}")
            except Exception as e:
                logger.warning(
                    f"Failed to queue notification task for post {instance.pk}: {e}"
                )

        # Ping search engines on publish (ASYNC)
        if created and is_published:
            try:
                from apps.blog.tasks import ping_search_engines

                sitemap_url = (
                    getattr(settings, "SITE_URL", "").rstrip("/") + "/sitemap.xml"
                )
                if sitemap_url.startswith("http"):
                    ping_search_engines.delay(sitemap_url)  # type: ignore[attr-defined]
                    logger.info(
                        f"Queued search engine ping task for post {instance.pk}"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to queue search engine ping for post {instance.pk}: {e}"
                )

        # Scan published post body for in-content ad placement (ASYNC)
        if instance.status == PostStatus.PUBLISHED:
            try:
                from apps.ads.tasks import scan_blog_post_content

                scan_blog_post_content.delay(instance.pk)  # type: ignore[attr-defined]
                logger.info(f"Queued ad content scan for post {instance.pk}")
            except Exception as e:
                logger.debug("Failed to queue ad scan for post %s: %s", instance.pk, e)

        # Emit blog_post_published Django signal for cross-app listeners
        # (tag updates and other supported app integrations)
        if is_published:
            try:
                from apps.core.signals import blog_post_published

                blog_post_published.send(
                    sender=Post, post=instance, user=instance.author
                )
            except Exception as e:
                logger.debug(
                    "Failed to emit blog_post_published for %s: %s",
                    instance.pk,
                    e,
                )

    post_save.connect(
        post_after_save,
        sender=Post,
        weak=False,
        dispatch_uid="blog_post_after_save",
    )
