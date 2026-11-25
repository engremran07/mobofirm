"""Member-submitted changes to existing live blog posts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Field
from django.utils import timezone

from apps.blog.models import (
    Category,
    Post,
    PostSource,
    PostStatus,
    PostUpdateProposal,
    PostUpdateProposalStatus,
)
from apps.blog.services import contributions
from apps.blog.services.history import PostHistoryService
from apps.blog.services.workflow import can_moderate
from apps.core.editor.forms import clean_rich_text
from apps.tags.models import Tag
from apps.tags.selectors import public_tags

PROPOSABLE_FIELDS = frozenset({"title", "summary", "body", "category_id", "tags"})
MAX_REVIEW_REASON = 1000
STALE_REASON = (
    "The article changed after this proposal was submitted. Please submit an "
    "updated proposal."
)


@dataclass(frozen=True)
class ProposalResult:
    """Submission result with an idempotency indicator."""

    proposal: PostUpdateProposal
    created: bool = True


def _verified_member(user: Any) -> bool:
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and user.is_active
        and contributions.is_verified(user)
    )


def _require_target(post: Post) -> None:
    if (
        post.status != PostStatus.PUBLISHED
        or (post.publish_at is not None and post.publish_at > timezone.now())
        or post.content_source == PostSource.AUTOMATED
    ):
        raise ValidationError("Only live, human-authored posts can be updated.")


def _validate_changes(post: Post, changes: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise ValidationError("Choose at least one post field to update.")
    if not set(changes) <= PROPOSABLE_FIELDS:
        raise ValidationError("The proposal contains an unsupported field.")

    validated: dict[str, Any] = {}
    for name in ("title", "summary", "body"):
        if name not in changes:
            continue
        value = changes[name]
        if not isinstance(value, str):
            raise ValidationError(f"The proposed {name} must be text.")
        if name == "body":
            value = clean_rich_text(
                value, "blog_contribution", required=True, visible_limit=50000
            )
        field = Post._meta.get_field(name)
        if not isinstance(field, Field):
            raise TypeError(f"Post field {name!r} is not a concrete model field.")
        validated[name] = field.clean(value, post)

    if "category_id" in changes:
        category_id = changes["category_id"]
        if category_id is not None and (
            isinstance(category_id, bool)
            or not isinstance(category_id, int)
            or not Category.objects.filter(pk=category_id).exists()
        ):
            raise ValidationError("Choose an available category.")
        validated["category_id"] = category_id

    if "tags" in changes:
        raw_tags = changes["tags"]
        if not isinstance(raw_tags, list) or any(
            isinstance(tag_id, bool) or not isinstance(tag_id, int)
            for tag_id in raw_tags
        ):
            raise ValidationError("The proposed tag list is invalid.")
        tag_ids = set(raw_tags)
        allowed_ids = set(public_tags().values_list("pk", flat=True))
        allowed_ids.update(post.tags.values_list("pk", flat=True))
        if not tag_ids <= allowed_ids:
            raise ValidationError("One or more selected tags are unavailable.")
        validated["tags"] = sorted(tag_ids)
    return validated


def submit(user: Any, post: Post, changes: dict[str, Any]) -> ProposalResult:
    """Create one open proposal per member and post.

    Identical retries return the existing proposal without creating a duplicate.
    """
    if not _verified_member(user):
        raise PermissionDenied("A verified account is required.")

    with transaction.atomic():
        locked_post = Post.objects.select_for_update().get(pk=post.pk)
        _require_target(locked_post)
        validated = _validate_changes(locked_post, changes)
        current_tags = set(locked_post.tags.values_list("pk", flat=True))
        changed = any(
            getattr(locked_post, name) != value
            for name, value in validated.items()
            if name not in {"tags", "category_id"}
        )
        if "category_id" in validated:
            changed |= locked_post.category_id != validated["category_id"]
        if "tags" in validated:
            changed |= current_tags != set(validated["tags"])
        if not changed:
            raise ValidationError("The proposal does not change this article.")

        pending = PostUpdateProposal.objects.filter(
            post=locked_post,
            submitted_by=user,
            status=PostUpdateProposalStatus.PENDING,
        ).first()
        if pending is not None:
            if (
                pending.base_version == locked_post.version
                and pending.proposed_changes == validated
            ):
                return ProposalResult(pending, created=False)
            raise ValidationError(
                "You already have an update proposal waiting for this article."
            )

        try:
            with transaction.atomic():
                proposal = PostUpdateProposal.objects.create(
                    post=locked_post,
                    submitted_by=user,
                    base_version=locked_post.version,
                    proposed_changes=validated,
                )
        except IntegrityError:
            pending = PostUpdateProposal.objects.filter(
                post=locked_post,
                submitted_by=user,
                status=PostUpdateProposalStatus.PENDING,
            ).first()
            if pending is None:
                raise
            if (
                pending.base_version == locked_post.version
                and pending.proposed_changes == validated
            ):
                return ProposalResult(pending, created=False)
            raise ValidationError(
                "You already have an update proposal waiting for this article."
            ) from None
        return ProposalResult(proposal)


def review(
    proposal_id: int,
    reviewer: Any,
    *,
    decision: str,
    reason: str = "",
    changes: dict[str, Any] | None = None,
) -> PostUpdateProposal:
    """Approve corrected fields or reject a proposal, preserving the post on failure."""
    if not can_moderate(reviewer):
        raise PermissionDenied("Staff blog-review permission is required.")
    if decision not in {"approve", "reject"}:
        raise ValidationError("Choose approve or reject.")

    reason = " ".join(reason.split())
    if len(reason) > MAX_REVIEW_REASON:
        raise ValidationError(
            f"Keep the review reason within {MAX_REVIEW_REASON} characters."
        )
    if decision == "reject" and not reason:
        raise ValidationError("Give the author a reason for rejecting the proposal.")

    with transaction.atomic():
        proposal = PostUpdateProposal.objects.select_for_update().get(pk=proposal_id)
        if proposal.status != PostUpdateProposalStatus.PENDING:
            raise ValidationError("This proposal has already been reviewed.")
        post = Post.objects.select_for_update().get(pk=proposal.post_id)
        if (
            post.version != proposal.base_version
            or post.status != PostStatus.PUBLISHED
            or (post.publish_at is not None and post.publish_at > timezone.now())
            or post.content_source == PostSource.AUTOMATED
        ):
            proposal.status = PostUpdateProposalStatus.STALE
            proposal.reviewed_by = reviewer
            proposal.reviewed_at = timezone.now()
            proposal.review_reason = STALE_REASON
            proposal.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_reason",
                    "updated_at",
                ]
            )
            return proposal

        if decision == "reject":
            proposal.status = PostUpdateProposalStatus.REJECTED
        else:
            proposed = proposal.proposed_changes
            reviewed = _validate_changes(post, proposed if changes is None else changes)
            if set(reviewed) != set(proposed):
                raise ValidationError(
                    "Keep the reviewed fields aligned with the proposal."
                )
            fields = {name: value for name, value in reviewed.items() if name != "tags"}
            tags: list[Tag] | None = None
            if "tags" in reviewed:
                tags = list(Tag.objects.filter(pk__in=reviewed["tags"]))
            update: dict[str, Any] = {
                "user": reviewer,
                "change_summary": f"Approved member update proposal #{proposal.pk}",
                **fields,
            }
            if tags is not None:
                update["tags"] = tags
            PostHistoryService.update_post(post, **update)
            proposal.proposed_changes = reviewed
            proposal.status = PostUpdateProposalStatus.APPROVED

        proposal.reviewed_by = reviewer
        proposal.reviewed_at = timezone.now()
        proposal.review_reason = reason
        proposal.save(
            update_fields=[
                "proposed_changes",
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_reason",
                "updated_at",
            ]
        )
        return proposal
