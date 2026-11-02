"""
Signal handlers for blog app
Responds to core app signals without creating circular dependencies.
"""

import logging

from django.dispatch import receiver

from apps.core.signals import (
    blog_post_deleted,
    blog_post_published,
    content_viewed,
)

logger = logging.getLogger(__name__)


@receiver(blog_post_published)
def invalidate_blog_cache(sender, post, **kwargs):
    """Clear blog-related caches when post is published"""
    from django.core.cache import cache

    cache.delete("blog_post_list")
    cache.delete(f"blog_post_{post.pk}")
    cache.delete("blog_latest_posts")
    logger.info(f"Cleared cache for published post: {post.pk}")


@receiver(blog_post_deleted)
def cleanup_blog_relationships(sender, post_id, **kwargs):
    """Clean up related data when blog post is deleted"""
    from django.core.cache import cache

    cache.delete("blog_post_list")
    cache.delete(f"blog_post_{post_id}")
    logger.info(f"Cleaned up data for deleted post: {post_id}")


@receiver(content_viewed)
def track_blog_view(sender, content_object, user, request, **kwargs):
    """Track blog post views"""
    from apps.blog.models import Post

    if isinstance(content_object, Post):
        # Increment view count using correct field name
        content_object.views_count = (content_object.views_count or 0) + 1
        content_object.save(update_fields=["views_count"])
        logger.debug(f"Tracked view for post: {content_object.pk}")
