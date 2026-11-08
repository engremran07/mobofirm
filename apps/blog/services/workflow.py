"""Blog post workflow: owner or staff request review, permitted staff publish."""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.blog.models import Post, PostAccess, PostStatus
from apps.core.utils.logging import log_event

logger = logging.getLogger(__name__)

PUBLISHABLE_STATUSES = {
    PostStatus.DRAFT,
    PostStatus.IN_REVIEW,
    PostStatus.SCHEDULED,
}


class WorkflowAction(enum.Enum):
    REQUEST_REVIEW = "request_review"
    PUBLISH = "publish"
    SCHEDULE = "schedule"
    ARCHIVE = "archive"


@dataclass
class WorkflowResult:
    ok: bool
    status: str
    message: str = ""


def can_moderate(user) -> bool:
    """Staff holding the blog change permission may publish, schedule and archive."""
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_staff
        and user.has_perm("blog.change_post")
    )


def _require_moderator(user) -> None:
    if not can_moderate(user):
        raise PermissionDenied("Staff publishing permission is required.")


def _audit(action: str, post: Post, user, **extra) -> None:
    log_event(
        logger,
        "info",
        f"blog.workflow.{action}",
        post=post.pk,
        actor=getattr(user, "pk", None),
        status=post.status,
        **extra,
    )


def request_review(post: Post, user=None) -> WorkflowResult:
    if user is None or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication is required.")
    if not (user.is_staff or post.author_id == user.pk):
        raise PermissionDenied("Only the post author or staff may request review.")
    if not post.title.strip() or not post.body.strip():
        raise ValidationError("A post requires a title and body before review.")
    if post.status not in {PostStatus.DRAFT, PostStatus.IN_REVIEW}:
        raise ValidationError("Only draft posts can enter review.")
    if post.status != PostStatus.IN_REVIEW:
        post.status = PostStatus.IN_REVIEW
        post.save(update_fields=["status", "is_published", "updated_at"])
        _audit("request_review", post, user)
    return WorkflowResult(ok=True, status=post.status, message="Submitted for review")


def _approve_price(post: Post, user) -> list[str]:
    """Staff publishing a gated post approves its proposed price; paid needs one."""
    if post.access == PostAccess.FREE:
        return []
    if post.price is None:
        if post.proposed_price is not None:
            post.price = post.proposed_price
            post.price_approved_by = user
            post.price_approved_at = timezone.now()
            return ["price", "price_approved_by", "price_approved_at"]
        if post.access == PostAccess.PAID:
            raise ValidationError(
                "A paid post needs an approved price before it can go live."
            )
    return []


def schedule(post: Post, when: datetime, user=None) -> WorkflowResult:
    _require_moderator(user)
    when = when or timezone.now()
    if timezone.is_naive(when):
        raise ValidationError("Scheduled publication time must include a timezone.")
    if post.status not in PUBLISHABLE_STATUSES:
        raise ValidationError("This post cannot be scheduled from its current status.")
    price_fields = _approve_price(post, user)
    post.status = PostStatus.SCHEDULED
    post.publish_at = when
    post.published_by = user
    post.save(
        update_fields=[
            "status",
            "publish_at",
            "published_by",
            "is_published",
            "updated_at",
            *price_fields,
        ]
    )
    _audit("schedule", post, user, publish_at=when.isoformat())
    return WorkflowResult(ok=True, status=post.status, message="Scheduled")


def publish(post: Post, user=None) -> WorkflowResult:
    _require_moderator(user)
    if post.status not in PUBLISHABLE_STATUSES:
        raise ValidationError("This post cannot be published from its current status.")
    price_fields = _approve_price(post, user)
    now = timezone.now()
    post.status = PostStatus.PUBLISHED
    post.publish_at = now
    post.published_at = post.published_at or now
    post.published_by = user
    post.save(
        update_fields=[
            "status",
            "publish_at",
            "published_at",
            "published_by",
            "is_published",
            "updated_at",
            *price_fields,
        ]
    )
    _audit("publish", post, user)
    return WorkflowResult(ok=True, status=post.status, message="Published")


def archive(post: Post, user=None) -> WorkflowResult:
    _require_moderator(user)
    post.status = PostStatus.ARCHIVED
    post.save(update_fields=["status", "is_published", "updated_at"])
    _audit("archive", post, user)
    return WorkflowResult(ok=True, status=post.status, message="Archived")
