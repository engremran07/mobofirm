"""Comment lifecycle: submit, edit, vote and report with automatic moderation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.utils import timezone

from apps.blog.models import Post
from apps.comments.models import (
    COMMENT_MAX_LENGTH,
    COMMENT_MIN_LENGTH,
    Comment,
    CommentReport,
    CommentSettings,
    CommentVote,
)
from apps.comments.services.moderation import ModerationResult, assess_comment
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)

MAX_THREAD_DEPTH = 3
EDIT_WINDOW = timedelta(hours=24)
DUPLICATE_WINDOW = timedelta(minutes=10)
MAX_HISTORY = 5

LABEL_TO_STATUS = {
    "approved": Comment.Status.APPROVED,
    "pending": Comment.Status.PENDING,
    "spam": Comment.Status.SPAM,
    "rejected": Comment.Status.REJECTED,
}


@dataclass(frozen=True)
class SubmissionResult:
    comment: Comment
    moderation: ModerationResult

    @property
    def published(self) -> bool:
        return self.comment.status == Comment.Status.APPROVED


def clean_body(body: str) -> str:
    text = (body or "").strip()
    if len(text) < COMMENT_MIN_LENGTH:
        raise ValidationError(
            f"Comments need at least {COMMENT_MIN_LENGTH} characters."
        )
    if len(text) > COMMENT_MAX_LENGTH:
        raise ValidationError(
            f"Comments are limited to {COMMENT_MAX_LENGTH} characters."
        )
    return text


def _depth(comment: Comment) -> int:
    depth = 1
    node = comment
    while node.parent_id is not None and depth <= MAX_THREAD_DEPTH:
        if node.parent is None:
            break
        node = node.parent
        depth += 1
    return depth


def _resolve_parent(post: Post, parent_id: int | str | None) -> Comment | None:
    if not parent_id:
        return None
    try:
        parent = Comment.objects.select_related("parent").get(
            pk=int(parent_id), post=post, is_deleted=False
        )
    except (Comment.DoesNotExist, ValueError, TypeError) as error:
        raise ValidationError(
            "The comment you are replying to no longer exists."
        ) from error
    if parent.status != Comment.Status.APPROVED:
        raise ValidationError("You can only reply to published comments.")
    if _depth(parent) >= MAX_THREAD_DEPTH:
        return parent.parent or parent
    return parent


def _assess(user, body: str, *, exclude_pk: int | None = None) -> ModerationResult:
    config = CommentSettings.get_solo()
    recent = Comment.objects.filter(
        user=user,
        body=body,
        created_at__gte=timezone.now() - DUPLICATE_WINDOW,
        is_deleted=False,
    )
    if exclude_pk:
        recent = recent.exclude(pk=exclude_pk)
    result = assess_comment(
        body, max_links=config.max_links, duplicate_of_recent=recent.exists()
    )
    if result.label == "approved" and not config.auto_approve_clean:
        return ModerationResult("pending", result.score, ("auto-approval is off",))
    return result


def _flags(result: ModerationResult, **extra) -> dict:
    return {
        "moderation": {
            "label": result.label,
            "score": result.score,
            "rationale": result.rationale,
            "reasons": list(result.reasons),
        },
        **extra,
    }


def submit_comment(
    post: Post, user, body: str, *, parent_id: int | str | None = None, locale: str = ""
) -> SubmissionResult:
    """Create a comment, auto-moderating it. Raises ``ValidationError`` on bad input."""
    text = clean_body(body)
    parent = _resolve_parent(post, parent_id)
    result = _assess(user, text)
    with transaction.atomic():
        comment = Comment.objects.create(
            post=post,
            user=user,
            body=text,
            parent=parent,
            status=LABEL_TO_STATUS[result.label],
            toxicity_score=result.score,
            metadata={"locale": locale} if locale else {},
            moderation_flags=_flags(result),
        )
        if comment.status == Comment.Status.APPROVED:
            transaction.on_commit(lambda: notify_published(comment.pk))
    log_event(
        logger,
        "info",
        "comments.submitted",
        comment=comment.pk,
        post=post.pk,
        status=comment.status,
        score=result.score,
    )
    return SubmissionResult(comment, result)


def edit_comment(comment: Comment, user, body: str) -> SubmissionResult:
    """Owners edit within the window; the edit is screened again before it shows."""
    if comment.user_id != user.pk:
        raise PermissionDenied("Only the author can edit a comment.")
    if comment.is_deleted:
        raise ValidationError("This comment was removed.")
    if timezone.now() - comment.created_at > EDIT_WINDOW:
        raise ValidationError("Comments can only be edited for 24 hours.")
    text = clean_body(body)
    result = _assess(user, text, exclude_pk=comment.pk)
    with transaction.atomic():
        locked = Comment.objects.select_for_update().get(pk=comment.pk)
        metadata = dict(locked.metadata or {})
        history = [
            *metadata.get("history", []),
            {"body": locked.body, "edited_at": timezone.now().isoformat()},
        ]
        metadata["history"] = history[-MAX_HISTORY:]
        locked.body = text
        locked.edited_at = timezone.now()
        locked.metadata = metadata
        locked.status = LABEL_TO_STATUS[result.label]
        locked.toxicity_score = result.score
        locked.moderation_flags = _flags(result, edited=True)
        locked.save()
    return SubmissionResult(locked, result)


def toggle_vote(comment: Comment, user) -> tuple[bool, int]:
    """Add or remove the user's upvote on a published comment; returns (voted, score)."""
    if comment.status != Comment.Status.APPROVED or comment.is_deleted:
        raise ValidationError("Only published comments can be voted on.")
    with transaction.atomic():
        vote, created = CommentVote.objects.get_or_create(comment=comment, user=user)
        if not created:
            vote.delete()
        Comment.objects.filter(pk=comment.pk).update(
            score=F("score") + (1 if created else -1)
        )
    comment.refresh_from_db(fields=["score"])
    return created, comment.score


def report_comment(
    comment: Comment, user, reason: str = CommentReport.Reason.OTHER
) -> tuple[bool, bool]:
    """Record a report; returns (new_report, requeued)."""
    if comment.is_deleted or comment.status != Comment.Status.APPROVED:
        raise ValidationError("Only published comments can be reported.")
    if comment.user_id == user.pk:
        raise ValidationError("You cannot report your own comment.")
    if reason not in CommentReport.Reason.values:
        reason = CommentReport.Reason.OTHER
    threshold = CommentSettings.get_solo().report_threshold
    with transaction.atomic():
        try:
            with transaction.atomic():
                CommentReport.objects.create(comment=comment, user=user, reason=reason)
        except IntegrityError:
            return False, False
        locked = Comment.objects.select_for_update().get(pk=comment.pk)
        distinct = CommentReport.objects.filter(comment=locked).aggregate(
            total=Count("user", distinct=True)
        )["total"]
        requeue = locked.status == Comment.Status.APPROVED and distinct >= threshold
        if requeue:
            flags = dict(locked.moderation_flags or {})
            flags["reports"] = {
                "count": distinct,
                "requeued_at": timezone.now().isoformat(),
            }
            locked.status = Comment.Status.PENDING
            locked.moderation_flags = flags
            locked.save()
    log_event(
        logger,
        "info",
        "comments.reported",
        comment=comment.pk,
        actor=user.pk,
        reason=reason,
        requeued=requeue,
    )
    return True, requeue


def notify_published(comment_id: int) -> None:
    from apps.comments.tasks import notify_comment_published

    notify_comment_published.delay(comment_id)
