"""Blog post service: draft creation and versioned updates.

Publication is never done here; it goes through ``apps.blog.services.workflow`` so
the review/publish permission checks stay in one place.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.blog.models import Post, PostStatus
from apps.blog.services.history import PostHistoryService

logger = logging.getLogger(__name__)


class PostService:
    @transaction.atomic
    def create_post(
        self, title: str, body: str, author, summary: str = "", category=None, **kwargs
    ) -> Post:
        """Create a draft with its first recorded version."""
        post = Post.objects.create(
            title=title,
            body=body,
            summary=summary or "",
            author=author,
            category=category,
            status=PostStatus.DRAFT,
            **kwargs,
        )
        PostHistoryService.record_current_version(
            post, user=author, change_summary="Initial version"
        )
        logger.info("Created draft post %s", post.pk)
        return post

    @transaction.atomic
    def update_post(
        self, post: Post, updated_by, change_summary: str = "", **fields
    ) -> Post:
        """Update a post, recording a new version when versioned content changes."""
        return PostHistoryService.update_post(
            post,
            user=updated_by,
            change_summary=change_summary or "Updated post",
            **fields,
        )


__all__ = ["PostService"]
