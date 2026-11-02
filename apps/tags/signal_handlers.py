"""
Signal handlers for tags app
Responds to content signals without creating circular dependencies.
"""

import logging

from django.dispatch import receiver

from apps.core.signals import blog_post_deleted, blog_post_published

logger = logging.getLogger(__name__)


@receiver(blog_post_deleted)
def cleanup_orphaned_tags(sender, post_id, **kwargs):
    """Remove tags with no posts when a post is deleted"""
    try:
        from apps.tags.models import Tag

        # Find tags with no posts
        orphaned_tags = Tag.objects.filter(posts__isnull=True)
        count = orphaned_tags.count()

        if count > 0:
            orphaned_tags.delete()
            logger.info(
                f"Cleaned up {count} orphaned tags after post {post_id} deletion"
            )
    except Exception as exc:
        logger.exception(f"Failed to cleanup orphaned tags: {exc}")


@receiver(blog_post_published)
def update_tag_counts(sender, post, **kwargs):
    """Update tag usage counts when post is published"""
    try:
        from django.core.cache import cache

        if hasattr(post, "tags"):
            for tag in post.tags.all():
                cache_key = f"tag_count_{tag.pk}"
                cache.delete(cache_key)

            logger.debug(f"Updated tag counts for post: {post.pk}")
    except Exception as exc:
        logger.exception(f"Failed to update tag counts: {exc}")
