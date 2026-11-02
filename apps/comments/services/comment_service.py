"""
Enterprise-grade Comment Service Layer.
Handles all comment business logic with reactions, voting, moderation, threading, and analytics.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.comments.models import Comment
from apps.core.events import EventTypes, event_bus

# ARCHIVED: Enhanced models moved to apps/core/versions/
# from apps.comments.models_enhanced import (
#     CommentReaction, CommentVote, CommentFlag, CommentMention,
#     CommentEdit, CommentBookmark, CommentAward, CommentAnalytics,
#     CommentThread, ModerationAction
# )
from apps.core.infrastructure import EmailService, QueueService
from apps.core.metrics import metrics

logger = logging.getLogger(__name__)


class CommentService:
    """
    Centralized service for all comment operations.
    Provides enterprise-grade features for comments system.
    """

    def __init__(self):
        self.queue = QueueService()
        self.email = EmailService()

    # ==================== CRUD Operations ====================

    @transaction.atomic
    def create_comment(
        self,
        content_object: Any,
        user: Any,
        body: str,
        parent: Comment | None = None,
        auto_approve: bool = False,
        metadata: dict | None = None,
    ) -> Comment:
        """
        Create a new comment with full validation and processing.

        Args:
            content_object: Any Django model to comment on
            user: User making the comment
            body: Comment text
            parent: Optional parent comment for threading
            auto_approve: Whether to auto-approve (bypasses moderation)
            metadata: Optional metadata dict

        Returns:
            Created Comment instance
        """
        metrics.increment("comment.create")

        # Get content type
        content_type = ContentType.objects.get_for_model(content_object)

        # Create comment
        comment = Comment.objects.create(
            content_type=content_type,
            object_id=content_object.pk,
            content_object=content_object,
            user=user,
            body=body,
            parent=parent,
            status=Comment.Status.APPROVED if auto_approve else Comment.Status.PENDING,
            is_approved=auto_approve,
            metadata=metadata or {},
        )

        # Extract and create mentions
        mentions = self._extract_mentions(body)
        if mentions:
            self._create_mentions(comment, mentions)

        # Create analytics record
        CommentAnalytics.objects.create(comment=comment)  # noqa: F821  # type: ignore[name-defined]

        # Update thread metadata if this is a reply
        if parent:
            self._update_thread_metadata(parent)
        else:
            # Create thread for root comments
            CommentThread.objects.create(  # noqa: F821  # type: ignore[name-defined]
                root_comment=comment,
                total_replies=0,
                max_depth=0,
                last_activity=timezone.now(),
                participant_count=1,
            )

        # Publish event
        event_bus.publish(
            EventTypes.COMMENT_CREATED,
            {
                "comment_id": comment.pk,
                "user_id": user.id,
                "content_type": str(content_type),
                "object_id": content_object.pk,
            },
        )

        # Queue moderation if not auto-approved
        if not auto_approve:
            self.queue.enqueue(
                "apps.comments.tasks.moderate_comment", comment_id=comment.pk
            )

        logger.info(f"Comment {comment.pk} created by user {user.id}")
        return comment

    @transaction.atomic
    def update_comment(
        self, comment: Comment, new_body: str, edited_by: Any, edit_reason: str = ""
    ) -> Comment:
        """
        Update comment body with edit tracking.
        """
        metrics.increment("comment.update")

        # Save edit history
        CommentEdit.objects.create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment,
            previous_body=comment.body,
            edited_by=edited_by,
            edit_reason=edit_reason,
        )

        # Update comment
        comment.body = new_body
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])

        # Re-extract mentions
        self._update_mentions(comment, new_body)

        # Queue re-moderation if needed
        if comment.status == Comment.Status.APPROVED:
            self.queue.enqueue(
                "apps.comments.tasks.remoderate_comment", comment_id=comment.pk
            )

        logger.info(f"Comment {comment.pk} updated by {edited_by.id}")
        return comment

    @transaction.atomic
    def delete_comment(
        self, comment: Comment, deleted_by: Any, hard_delete: bool = False
    ) -> bool:
        """
        Delete comment (soft or hard).
        """
        metrics.increment("comment.delete")

        if hard_delete:
            comment.delete()
        else:
            comment.is_deleted = True
            comment.deleted_at = timezone.now()
            comment.save(update_fields=["is_deleted", "deleted_at"])

        # Log moderation action
        ModerationAction.objects.create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment if not hard_delete else None,
            action=ModerationAction.ActionType.DELETE,  # noqa: F821  # type: ignore[name-defined]
            moderator=deleted_by,
            reason="Comment deleted",
        )

        logger.info(f"Comment {comment.pk} deleted by {deleted_by.id}")
        return True

    # ==================== Reactions & Voting ====================

    @transaction.atomic
    def add_reaction(
        self, comment: Comment, user: Any, reaction_type: str
    ) -> CommentReaction:  # noqa: F821  # type: ignore[name-defined]
        """
        Add or update user's reaction to comment.
        """
        metrics.increment("comment.reaction")

        reaction, _created = CommentReaction.objects.update_or_create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user, defaults={"reaction_type": reaction_type}
        )

        # Update analytics
        self._update_comment_analytics(comment)

        # Notify comment author
        if comment.user != user:
            self.queue.enqueue(
                "apps.comments.tasks.notify_comment_reaction",
                comment_id=comment.pk,
                user_id=user.id,
                reaction_type=reaction_type,
            )

        return reaction

    @transaction.atomic
    def remove_reaction(self, comment: Comment, user: Any) -> bool:
        """Remove user's reaction from comment."""
        deleted_count, _ = CommentReaction.objects.filter(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user
        ).delete()

        if deleted_count > 0:
            self._update_comment_analytics(comment)

        return deleted_count > 0

    @transaction.atomic
    def vote_comment(
        self,
        comment: Comment,
        user: Any,
        vote: int,  # 1 for upvote, -1 for downvote
    ) -> CommentVote:  # noqa: F821  # type: ignore[name-defined]
        """
        Add or update user's vote on comment.
        """
        metrics.increment(f"comment.vote.{'up' if vote > 0 else 'down'}")

        vote_obj, _created = CommentVote.objects.update_or_create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user, defaults={"vote": vote}
        )

        # Update comment score
        self._recalculate_comment_score(comment)

        # Update analytics
        self._update_comment_analytics(comment)

        return vote_obj

    @transaction.atomic
    def remove_vote(self, comment: Comment, user: Any) -> bool:
        """Remove user's vote from comment."""
        deleted_count, _ = CommentVote.objects.filter(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user
        ).delete()

        if deleted_count > 0:
            self._recalculate_comment_score(comment)
            self._update_comment_analytics(comment)

        return deleted_count > 0

    # ==================== Moderation ====================

    @transaction.atomic
    def flag_comment(
        self, comment: Comment, user: Any, reason: str, details: str = ""
    ) -> CommentFlag:  # noqa: F821  # type: ignore[name-defined]
        """
        Flag comment for moderation.
        """
        metrics.increment("comment.flag")

        flag, _created = CommentFlag.objects.get_or_create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user, defaults={"reason": reason, "details": details}
        )

        # Update analytics
        analytics = comment.analytics  # type: ignore[attr-defined]
        analytics.flag_count = comment.flags.filter(resolved=False).count()  # type: ignore[attr-defined]
        analytics.save(update_fields=["flag_count"])

        # Auto-hide if threshold reached
        flag_threshold = getattr(settings, "COMMENT_FLAG_THRESHOLD", 3)
        if analytics.flag_count >= flag_threshold:
            self.moderate_comment(
                comment,
                action="reject",
                moderator=None,  # System action
                reason=f"Auto-rejected: {analytics.flag_count} flags",
                auto_moderated=True,
            )

        return flag

    @transaction.atomic
    def moderate_comment(
        self,
        comment: Comment,
        action: str,  # approve, reject, delete, lock
        moderator: Any | None,
        reason: str = "",
        auto_moderated: bool = False,
    ) -> ModerationAction:  # noqa: F821  # type: ignore[name-defined]
        """
        Moderate comment with specified action.
        """
        metrics.increment(f"comment.moderate.{action}")

        action_map = {
            "approve": Comment.Status.APPROVED,
            "reject": Comment.Status.REJECTED,
            "spam": Comment.Status.SPAM,
        }

        if action in action_map:
            comment.status = action_map[action]
            comment.is_approved = action == "approve"
            comment.save(update_fields=["status", "is_approved"])
        elif action == "delete":
            comment.is_deleted = True
            comment.deleted_at = timezone.now()
            comment.save(update_fields=["is_deleted", "deleted_at"])

        # Log action
        mod_action = ModerationAction.objects.create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment,
            action=action,
            moderator=moderator,
            reason=reason,
            auto_moderated=auto_moderated,
        )

        logger.info(
            f"Comment {comment.pk} moderated: {action} by {moderator or 'system'}"
        )
        return mod_action

    # ==================== Threading ====================

    def get_comment_thread(
        self,
        root_comment: Comment,
        max_depth: int = 10,
        sort_by: str = "score",  # score, date, votes
    ) -> list[dict]:
        """
        Get complete comment thread with nested structure.
        """
        cache_key = f"comment_thread:{root_comment.pk}:{sort_by}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Build thread recursively
        thread = self._build_thread_recursive(
            root_comment, depth=0, max_depth=max_depth, sort_by=sort_by
        )

        cache.set(cache_key, thread, timeout=300)  # 5 minutes
        return thread  # type: ignore[return-value]

    def _build_thread_recursive(
        self, comment: Comment, depth: int, max_depth: int, sort_by: str
    ) -> dict:  # type: ignore[return]
        """Recursively build comment thread."""
        if depth >= max_depth:
            return None  # type: ignore[return-value]

        # Get direct replies
        replies = (
            comment.children.filter(is_deleted=False, status=Comment.Status.APPROVED)  # type: ignore[attr-defined]
            .select_related("user")
            .prefetch_related("reactions", "votes")
        )

        # Sort replies
        if sort_by == "score":
            replies = replies.order_by("-score", "-created_at")
        elif sort_by == "votes":
            replies = replies.annotate(vote_count=Count("votes")).order_by(
                "-vote_count", "-created_at"
            )
        else:  # date
            replies = replies.order_by("-created_at")

        # Build structure
        thread = {"comment": comment, "depth": depth, "replies": []}

        for reply in replies:
            child_thread = self._build_thread_recursive(
                reply, depth + 1, max_depth, sort_by
            )
            if child_thread:
                thread["replies"].append(child_thread)

        return thread

    # ==================== Awards & Gamification ====================

    @transaction.atomic
    def give_award(
        self,
        comment: Comment,
        award_type: str,
        given_by: Any | None = None,
        auto_assigned: bool = False,
    ) -> CommentAward:  # noqa: F821  # type: ignore[name-defined]
        """
        Give award to comment.
        """
        metrics.increment(f"comment.award.{award_type}")

        award = CommentAward.objects.create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment,
            award_type=award_type,
            given_by=given_by,
            auto_assigned=auto_assigned,
        )

        # Update analytics
        analytics = comment.analytics  # type: ignore[attr-defined]
        analytics.award_count = comment.awards.count()  # type: ignore[attr-defined]
        analytics.calculate_engagement_score()
        analytics.save()

        # Notify comment author
        if comment.user != given_by:
            self.queue.enqueue(
                "apps.comments.tasks.notify_comment_award",
                comment_id=comment.pk,
                award_type=award_type,
            )

        return award

    @transaction.atomic
    def bookmark_comment(
        self, comment: Comment, user: Any, notes: str = ""
    ) -> CommentBookmark:  # noqa: F821  # type: ignore[name-defined]
        """
        Bookmark comment for user.
        """
        bookmark, created = CommentBookmark.objects.get_or_create(  # noqa: F821  # type: ignore[name-defined]
            comment=comment, user=user, defaults={"notes": notes}
        )

        if not created and notes:
            bookmark.notes = notes
            bookmark.save(update_fields=["notes"])

        # Update analytics
        analytics = comment.analytics  # type: ignore[attr-defined]
        analytics.bookmark_count = comment.bookmarks.count()  # type: ignore[attr-defined]
        analytics.save(update_fields=["bookmark_count"])

        return bookmark

    # ==================== Analytics ====================

    @transaction.atomic
    def update_comment_analytics(self, comment_id: int):
        """
        Update analytics for specific comment.
        """
        try:
            comment = Comment.objects.select_related("analytics").get(id=comment_id)
        except Comment.DoesNotExist:
            return

        analytics = comment.analytics  # type: ignore[attr-defined]

        # Count reactions
        analytics.reaction_count = comment.reactions.count()  # type: ignore[attr-defined]

        # Count votes
        votes = comment.votes.aggregate(  # type: ignore[attr-defined]
            upvotes=Count("id", filter=Q(vote=1)),
            downvotes=Count("id", filter=Q(vote=-1)),
        )
        analytics.upvotes = votes["upvotes"] or 0
        analytics.downvotes = votes["downvotes"] or 0
        analytics.net_votes = analytics.upvotes - analytics.downvotes

        # Count other metrics
        analytics.reply_count = comment.children.filter(is_deleted=False).count()  # type: ignore[attr-defined]
        analytics.flag_count = comment.flags.filter(resolved=False).count()  # type: ignore[attr-defined]
        analytics.bookmark_count = comment.bookmarks.count()  # type: ignore[attr-defined]
        analytics.award_count = comment.awards.count()  # type: ignore[attr-defined]

        # Calculate engagement score
        analytics.calculate_engagement_score()

        analytics.save()

        logger.debug(f"Updated analytics for comment {comment_id}")

    def get_top_comments(
        self,
        content_object: Any,
        limit: int = 10,
        period: str = "all",  # all, day, week, month
    ) -> list[Comment]:
        """
        Get top comments by engagement score.
        """
        content_type = ContentType.objects.get_for_model(content_object)

        qs = Comment.objects.filter(
            content_type=content_type,
            object_id=content_object.pk,
            status=Comment.Status.APPROVED,
            is_deleted=False,
        ).select_related("user", "analytics")

        # Filter by period
        if period != "all":
            period_map = {"day": 1, "week": 7, "month": 30}
            days = period_map.get(period, 7)
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(created_at__gte=cutoff)

        # Order by engagement
        qs = qs.order_by("-analytics__engagement_score", "-created_at")

        return list(qs[:limit])

    # ==================== Helper Methods ====================

    def _extract_mentions(self, text: str) -> list[str]:
        """Extract @mentions from text."""
        pattern = r"@(\w+)"
        return re.findall(pattern, text)

    def _create_mentions(self, comment: Comment, usernames: list[str]):
        """Create mention records and notifications."""
        from django.contrib.auth import get_user_model

        User = get_user_model()

        for username in usernames:
            try:
                user = User.objects.get(username=username)
                CommentMention.objects.get_or_create(  # noqa: F821  # type: ignore[name-defined]
                    comment=comment, mentioned_user=user
                )

                # Queue notification
                self.queue.enqueue(
                    "apps.comments.tasks.notify_mention",
                    comment_id=comment.pk,
                    user_id=user.pk,
                )
            except User.DoesNotExist:
                continue

    def _update_mentions(self, comment: Comment, new_text: str):
        """Update mentions after comment edit."""
        new_mentions = set(self._extract_mentions(new_text))
        old_mentions = set(
            comment.mentions.values_list("mentioned_user__username", flat=True)  # type: ignore[attr-defined]
        )

        # Remove old mentions not in new text
        removed = old_mentions - new_mentions
        if removed:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            user_ids = User.objects.filter(username__in=removed).values_list(
                "id", flat=True
            )
            comment.mentions.filter(mentioned_user_id__in=user_ids).delete()  # type: ignore[attr-defined]

        # Add new mentions
        added = new_mentions - old_mentions
        if added:
            self._create_mentions(comment, list(added))

    def _update_thread_metadata(self, root_comment: Comment):
        """Update thread metadata after new reply."""
        thread, _ = CommentThread.objects.get_or_create(  # noqa: F821  # type: ignore[name-defined]
            root_comment=root_comment,
            defaults={
                "total_replies": 0,
                "max_depth": 0,
                "last_activity": timezone.now(),
                "participant_count": 1,
            },
        )

        # Count all replies in thread
        total_replies = Comment.objects.filter(
            parent=root_comment, is_deleted=False
        ).count()

        thread.total_replies = total_replies
        thread.last_activity = timezone.now()
        thread.save(update_fields=["total_replies", "last_activity"])

    def _recalculate_comment_score(self, comment: Comment):
        """Recalculate comment score from votes."""
        votes = comment.votes.aggregate(total=Sum("vote"))  # type: ignore[attr-defined]
        comment.score = votes["total"] or 0
        comment.save(update_fields=["score"])

    def _update_comment_analytics(self, comment: Comment):
        """Update analytics (async via queue)."""
        self.queue.enqueue(
            "apps.comments.tasks.update_comment_analytics", comment_id=comment.pk
        )
