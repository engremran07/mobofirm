"""
Blog Post Service
=================

Business logic for blog post management.
"""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class PostService:
    """
    Business logic for post management.
    Keeps views thin and logic testable.

    Usage:
        service = PostService()
        post = service.create_post("Title", "Body", author=user)
        service.publish_post(post, publish_now=True)
    """

    def __init__(self):
        try:
            from apps.core.infrastructure import EmailService, QueueService

            self.queue = QueueService()
            self.email = EmailService()
        except ImportError:
            logger.warning("Infrastructure services not available")
            self.queue = None
            self.email = None

    @transaction.atomic
    def create_post(
        self, title: str, body: str, author, summary: str = "", category=None, **kwargs
    ):
        """
        Create a new blog post with versioning.

        Args:
            title: Post title
            body: Post content (HTML)
            author: User creating the post
            summary: Optional summary
            category: Optional category
            **kwargs: Additional fields

        Returns:
            Created Post instance
        """
        from apps.blog.models import Post, PostStatus
        from apps.core.utils.text import reading_time

        # Calculate reading time
        read_time = reading_time(body)

        post = Post.objects.create(
            title=title,
            body=body,
            summary=summary or "",
            author=author,
            category=category,
            status=PostStatus.DRAFT,
            reading_time=read_time,
            **kwargs,
        )

        # Create initial version
        self._create_version(post, author, "Initial version")

        logger.info(f"Created post: {post.title} (ID: {post.pk})")

        # Publish event
        try:
            from apps.core.events import EventTypes, event_bus

            event_bus.publish(EventTypes.BLOG_POST_CREATED, {"post_id": post.pk})
        except ImportError:
            pass

        return post

    @transaction.atomic
    def publish_post(
        self,
        post,
        publish_now: bool = True,
        schedule_at: datetime.datetime | None = None,
    ):
        """
        Publish a post immediately or schedule for later.

        Args:
            post: Post instance
            publish_now: Publish immediately if True
            schedule_at: Schedule publication for this datetime

        Returns:
            Updated Post instance
        """
        from apps.blog.models import PostStatus

        if publish_now:
            post.status = PostStatus.PUBLISHED
            post.published_at = timezone.now()
            post.is_published = True
            post.save()

            logger.info(f"Published post: {post.title}")

            # Trigger post-publish tasks
            self._on_post_published(post)

        else:
            post.status = PostStatus.SCHEDULED
            post.publish_at = schedule_at
            post.save()

            logger.info(f"Scheduled post: {post.title} for {schedule_at}")

            # Schedule publication task
            if self.queue and schedule_at:
                delay = (schedule_at - timezone.now()).total_seconds()
                if delay > 0:
                    self.queue.enqueue_in(
                        "apps.blog.tasks.publish_scheduled_post",
                        int(delay),
                        post_id=post.pk,
                    )

        return post

    def _on_post_published(self, post):
        """Post-publish hooks"""

        # Publish event
        try:
            from apps.core.events import EventTypes, event_bus

            event_bus.publish(
                EventTypes.BLOG_POST_PUBLISHED,
                {
                    "post_id": post.pk,
                    "title": post.title,
                    "author_id": post.author.id,
                },
            )
        except ImportError:
            pass

        # Notify subscribers (async if queue available)
        if self.queue:
            self.queue.enqueue("apps.blog.tasks.notify_subscribers", post_id=post.pk)

        # Update sitemap
        if self.queue:
            self.queue.enqueue("apps.seo.tasks.update_sitemap")

        # Auto-share if distribution app available
        try:
            from apps.distribution.services import (
                create_plan_for_post,
                should_fanout,
            )

            if should_fanout(post):
                create_plan_for_post(post)
        except ImportError:
            pass

    @transaction.atomic
    def update_post(self, post, updated_by, change_summary: str = "", **fields):
        """
        Update post with versioning.

        Args:
            post: Post instance
            updated_by: User making the update
            change_summary: Description of changes
            **fields: Fields to update

        Returns:
            Updated Post instance
        """
        from apps.core.utils.text import reading_time

        # Create version snapshot before update
        self._create_version(post, updated_by, change_summary)

        # Update fields
        for key, value in fields.items():
            setattr(post, key, value)

        # Recalculate reading time if body changed
        if "body" in fields:
            post.reading_time = reading_time(fields["body"])

        post.version += 1
        post.save()

        logger.info(f"Updated post: {post.title} (v{post.version})")

        # Publish event
        try:
            from apps.core.events import EventTypes, event_bus

            event_bus.publish(EventTypes.BLOG_POST_UPDATED, {"post_id": post.pk})
        except ImportError:
            pass

        return post

    def _create_version(self, post, user, summary: str):
        """Create version snapshot"""
        try:
            from apps.blog.models_versioning import PostVersion

            PostVersion.objects.create(
                post=post,
                version_number=post.version,
                title=post.title,
                summary=post.summary,
                body=post.body,
                created_by=user,
                change_summary=summary,
            )

            logger.debug(f"Created version {post.version} for post {post.pk}")
        except ImportError:
            logger.warning("PostVersion model not available")

    def get_related_posts(self, post, limit: int = 5) -> list:
        """
        Get related posts based on category and tags.

        Args:
            post: Post instance
            limit: Maximum number of related posts

        Returns:
            List of related Post instances
        """
        from apps.blog.models import Post, PostStatus

        related = Post.objects.filter(
            status=PostStatus.PUBLISHED, is_published=True
        ).exclude(id=post.pk)

        # Prioritize same category
        if post.category:
            related = related.filter(category=post.category)

        # Order by published date
        return list(related.order_by("-published_at")[:limit])

    def get_trending_posts(self, days: int = 7, limit: int = 10) -> list:
        """
        Get trending posts (recent, can integrate with analytics later).

        Args:
            days: Look back this many days
            limit: Maximum number of posts

        Returns:
            List of trending Post instances
        """
        from datetime import timedelta

        from apps.blog.models import Post, PostStatus

        cutoff = timezone.now() - timedelta(days=days)

        return list(
            Post.objects.filter(
                status=PostStatus.PUBLISHED, is_published=True, published_at__gte=cutoff
            ).order_by("-published_at")[:limit]
        )

    def get_featured_posts(self, limit: int = 5) -> list:
        """
        Get featured posts.

        Args:
            limit: Maximum number of posts

        Returns:
            List of featured Post instances
        """
        from apps.blog.models import Post, PostStatus

        return list(
            Post.objects.filter(
                status=PostStatus.PUBLISHED, is_published=True, featured=True
            ).order_by("-published_at")[:limit]
        )

    def archive_post(self, post, archived_by):
        """
        Archive a post (soft action).

        Args:
            post: Post instance
            archived_by: User archiving the post

        Returns:
            Updated Post instance
        """
        from apps.blog.models import PostStatus

        post.status = PostStatus.ARCHIVED
        post.save()

        logger.info(f"Archived post: {post.title}")

        return post

    @transaction.atomic
    def delete_post(self, post, deleted_by):
        """
        Soft delete a post.

        Args:
            post: Post instance
            deleted_by: User deleting the post

        Returns:
            Updated Post instance
        """
        # Use soft delete if available
        if hasattr(post, "soft_delete"):
            post.soft_delete(user=deleted_by)
        else:
            post.delete()

        logger.info(f"Deleted post: {post.title}")

        # Publish event
        try:
            from apps.core.events import EventTypes, event_bus

            event_bus.publish(EventTypes.BLOG_POST_DELETED, {"post_id": post.pk})
        except ImportError:
            pass

        return post


def search_posts(query: str, limit: int = 20):
    """Search published posts using PostgreSQL full-text search."""
    from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

    from apps.blog.models import Post, PostStatus

    now = timezone.now()
    vector = (
        SearchVector("title", weight="A")
        + SearchVector("summary", weight="B")
        + SearchVector("body", weight="C")
    )
    search_query = SearchQuery(query, search_type="plain")
    return (
        Post.objects.filter(status=PostStatus.PUBLISHED, publish_at__lte=now)
        .annotate(rank=SearchRank(vector, search_query))
        .filter(rank__gte=0.05)
        .select_related("author", "category")
        .prefetch_related("tags")
        .order_by("-rank", "-published_at")[:limit]
    )


__all__ = ["PostService", "search_posts"]
