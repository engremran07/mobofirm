"""
Enhanced notification signals for comment interactions.
Sends notifications for replies, mentions, reactions, and moderation actions.

ARCHIVED: Signal receivers for enhanced models (CommentReaction, CommentVote, CommentMention, CommentAward)
have been disabled as their models were moved to apps/core/versions/

Core signals (notify_on_comment) remain active for the base Comment model.
"""

from __future__ import annotations

import logging

from django.apps import apps
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _get_notification_helpers():
    """Lazy import notification helpers for modularity - users app is optional."""
    if not apps.is_installed("apps.users"):
        return None, lambda: False
    try:
        from apps.users.services.notifications import (
            notifications_enabled,
            send_notification,
        )

        return send_notification, notifications_enabled
    except Exception:
        return None, lambda: False


@receiver(post_save, sender="comments.Comment")
def notify_on_comment(sender, instance, created, **kwargs):
    """
    Send notifications when:
    - Someone comments on a post (notify post author)
    - Someone replies to a comment (notify parent comment author)
    """
    send_notification, notifications_enabled = _get_notification_helpers()

    if not created or not notifications_enabled():
        return

    if send_notification is None:
        return

    try:
        # Don't notify if comment is pending or spam
        if instance.status in ["pending", "spam"]:
            return

        # Notify post author when someone comments
        if instance.post and instance.post.author != instance.user:
            send_notification(
                recipient=instance.post.author,
                title="New comment on your post",
                message=f'{instance.user.get_full_name()} commented on "{instance.post.title}"',
                level="info",
                url=instance.post.get_absolute_url() + f"#comment-{instance.pk}",
                actor=instance.user,
                channel="web",
                action_type="comment",
                icon="comment",
            )

        # Notify parent comment author when someone replies
        if instance.parent and instance.parent.user != instance.user:
            send_notification(
                recipient=instance.parent.user,
                title="New reply to your comment",
                message=f"{instance.user.get_full_name()} replied to your comment",
                level="info",
                url=(instance.post.get_absolute_url() if instance.post else "#")
                + f"#comment-{instance.pk}",
                actor=instance.user,
                channel="web",
                action_type="reply",
                icon="reply",
            )
    except Exception as exc:
        logger.exception("Failed to send comment notification: %s", exc)


@receiver(post_save, sender="comments.Comment")
def sync_post_comment_count_on_save(sender, instance, created, **kwargs):
    """
    Sync the comment count on the related post when a comment is created or status changes.
    Only counts approved comments.
    """
    try:
        if not instance.post:
            return

        from django.db.models import Q

        from apps.blog.models import Post

        # Get approved comment count
        approved_count = instance.post.comments.filter(
            Q(status="approved") | Q(status="APPROVED")
        ).count()

        # Update post comment count if it has changed
        if (
            hasattr(instance.post, "comments_count")
            and instance.post.comments_count != approved_count
        ):
            Post.objects.filter(pk=instance.post.pk).update(
                comments_count=approved_count
            )
            logger.debug(
                f"Updated comment count for post {instance.post.pk}: {approved_count}"
            )

    except Exception as exc:
        logger.exception(f"Failed to sync comment count: {exc}")


@receiver(post_delete, sender="comments.Comment")
def sync_post_comment_count_on_delete(sender, instance, **kwargs):
    """
    Sync the comment count on the related post when a comment is deleted.
    """
    try:
        if not instance.post:
            return

        from django.db.models import Q

        from apps.blog.models import Post

        # Get approved comment count (instance already deleted)
        approved_count = instance.post.comments.filter(
            Q(status="approved") | Q(status="APPROVED")
        ).count()

        if hasattr(instance.post, "comments_count"):
            Post.objects.filter(pk=instance.post.pk).update(
                comments_count=approved_count
            )
            logger.debug(
                f"Updated comment count after delete for post {instance.post.pk}: {approved_count}"
            )

    except Exception as exc:
        logger.exception(f"Failed to sync comment count on delete: {exc}")


# ARCHIVED: Enhanced model signal receivers
# The following receivers depend on enhanced models that have been archived.
# To restore them, reintegrate the models from apps/core/versions/

# @receiver(post_save, sender='comments.CommentReaction')
# def notify_on_reaction(sender, instance, created, **kwargs):
#     """Notify comment author when someone reacts to their comment."""
#     ...

# @receiver(post_save, sender='comments.CommentVote')
# def notify_on_vote(sender, instance, created, **kwargs):
#     """Notify comment author when their comment gets upvoted."""
#     ...

# @receiver(post_save, sender='comments.CommentMention')
# def notify_on_mention(sender, instance, created, **kwargs):
#     """Notify user when they're mentioned in a comment."""
#     ...

# @receiver(post_save, sender='comments.CommentAward')
# def notify_on_award(sender, instance, created, **kwargs):
#     """Notify comment author when they receive an award."""
#     ...
#     try:
#         comment = instance.comment
#         if comment.user != instance.awarded_by:
#             award_emoji = {
#                 'gold': '🏆',
#                 'silver': '🥈',
#                 'bronze': '🥉',
#                 'helpful': '✨',
#                 'insightful': '💡',
#                 'funny': '😂',
#             }.get(instance.award_type, '🏅')
#
#             send_notification(
#                 recipient=comment.user,
#                 title=f"You received a {instance.award_type} award!",
#                 message=f"{instance.awarded_by.get_full_name()} awarded your comment {award_emoji}",
#                 level="info",
#                 url=(comment.post.get_absolute_url() if comment.post else "#") + f"#comment-{comment.pk}",
#                 actor=instance.awarded_by,
#                 channel="web",
#                 action_type="award",
#                 icon="award",
#             )
#     except Exception as exc:
#         logger.exception("Failed to send award notification: %s", exc)


# @receiver(post_save, sender='comments.ModerationAction')
# def notify_on_moderation(sender, instance, created, **kwargs):
#     """Notify comment author about moderation actions."""
#     ...
