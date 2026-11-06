"""Staff moderation decisions for comments, with an audit entry on the comment."""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.comments.models import Comment
from apps.comments.services.comment_service import notify_published
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)

DECISIONS = {
    "approve": Comment.Status.APPROVED,
    "reject": Comment.Status.REJECTED,
    "spam": Comment.Status.SPAM,
}
MAX_NOTE_LENGTH = 500


def moderate_comment(
    comment: Comment, decision: str, moderator, note: str = ""
) -> Comment:
    """Apply a staff decision and record who decided, when, and why."""
    if decision not in DECISIONS:
        raise ValidationError("Decision must be approve, reject, or spam.")
    if not (
        getattr(moderator, "is_authenticated", False)
        and moderator.is_staff
        and moderator.has_perm("comments.change_comment")
    ):
        raise PermissionDenied("Comment moderation permission is required.")
    note = (note or "").strip()[:MAX_NOTE_LENGTH]
    with transaction.atomic():
        locked = Comment.objects.select_for_update().get(pk=comment.pk)
        locked.status = DECISIONS[decision]
        locked.is_approved = locked.status == Comment.Status.APPROVED
        flags = dict(locked.moderation_flags or {})
        flags["decision"] = {
            "action": decision,
            "by": moderator.pk,
            "at": timezone.now().isoformat(),
            "note": note,
        }
        locked.moderation_flags = flags
        locked.save(update_fields=["status", "is_approved", "moderation_flags"])
        if locked.status == Comment.Status.APPROVED:
            transaction.on_commit(lambda: notify_published(locked.pk))
    log_event(
        logger,
        "info",
        "comments.moderation.decision",
        comment=locked.pk,
        decision=decision,
        actor=moderator.pk,
    )
    return locked
