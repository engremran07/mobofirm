"""Celery tasks for comments."""

from __future__ import annotations

import logging

from celery import shared_task
from django.apps import apps
from django.db import transaction

from apps.comments.models import Comment

logger = logging.getLogger(__name__)


@shared_task(
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=30,
    time_limit=60,
)
def notify_comment_published(comment_id: int) -> bool:
    """Tell the post author and the parent commenter about a newly visible comment.

    Idempotent: ``metadata["published_notified"]`` is claimed under a row lock, so
    duplicate delivery or a later re-approval never notifies twice.
    """
    if not apps.is_installed("apps.users"):
        return False
    from apps.users.services.notifications import send_notification

    with transaction.atomic():
        try:
            comment = (
                Comment.objects.select_for_update(of=("self",))
                .select_related("post__author", "parent__user", "user")
                .get(pk=comment_id)
            )
        except Comment.DoesNotExist:
            logger.warning("Comment %s vanished before notification", comment_id)
            return False
        if comment.status != Comment.Status.APPROVED or comment.is_deleted:
            return False
        metadata = dict(comment.metadata or {})
        if metadata.get("published_notified"):
            return False
        metadata["published_notified"] = True
        comment.metadata = metadata
        comment.save(update_fields=["metadata", "updated_at"])

    post = comment.post
    if post is None:
        return True
    link = f"{post.get_absolute_url()}#comment-{comment.pk}"
    name = comment.user.public_display_name
    if post.author_id != comment.user_id:
        send_notification(
            recipient=post.author,
            title="New comment on your post",
            message=f'{name} commented on "{post.title}"',
            url=link,
            actor=comment.user,
            channel="web",
            action_type="comment",
            icon="comment",
        )
    parent = comment.parent
    if parent is not None and parent.user_id not in {comment.user_id, post.author_id}:
        send_notification(
            recipient=parent.user,
            title="New reply to your comment",
            message=f"{name} replied to your comment",
            url=link,
            actor=comment.user,
            channel="web",
            action_type="reply",
            icon="reply",
        )
    return True
