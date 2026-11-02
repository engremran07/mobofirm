from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.governance.models import ReviewCase
from apps.governance.services import fulfill_case, submit_for_review
from apps.blog.models import Post, PostStatus


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


def request_review(post: Post, user=None) -> WorkflowResult:
    case = submit_for_review(post, actor=user)
    return WorkflowResult(
        ok=True,
        status=case.status,
        message=f"Submitted for governed review (case {case.pk})",
    )


def schedule(post: Post, when: datetime, user=None) -> WorkflowResult:
    case = _approved_case(post)
    fulfill_case(case.pk, user, scheduled_at=when or timezone.now())
    post.refresh_from_db()
    return WorkflowResult(ok=True, status=post.status, message="Scheduled")


def publish(post: Post, user=None) -> WorkflowResult:
    case = _approved_case(post)
    fulfill_case(case.pk, user)
    post.refresh_from_db()
    return WorkflowResult(ok=True, status=post.status, message="Published")


def archive(post: Post, user=None) -> WorkflowResult:
    if user is None or not user.has_perm("governance.can_publish_content"):
        raise PermissionDenied("Publishing permission is required to archive a post.")
    post.status = PostStatus.ARCHIVED
    post.save(update_fields=["status", "is_published", "updated_at"])
    return WorkflowResult(ok=True, status=post.status, message="Archived")


def _approved_case(post: Post) -> ReviewCase:
    content_type = ContentType.objects.get_for_model(post, for_concrete_model=False)
    case = ReviewCase.objects.filter(
        content_type=content_type,
        object_id=str(post.pk),
        status=ReviewCase.Status.APPROVED,
        is_open=True,
    ).first()
    if case is None:
        raise PermissionDenied("A completed governance review is required first.")
    return case
