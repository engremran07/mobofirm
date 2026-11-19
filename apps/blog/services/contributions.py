"""Member blog contributions: submit, edit, withdraw and notify reviewers.

A contribution is an ordinary :class:`~apps.blog.models.Post` authored by the member and
flagged ``is_community_contribution``. It enters the existing review workflow
(``PostStatus.IN_REVIEW``); only staff holding ``blog.change_post`` can publish it
through :mod:`apps.blog.services.workflow`, so a member can never publish.

Transaction and delivery contract:

* The post, its tags, the first revision and the review request are one atomic write,
  serialised per member by a row lock so concurrent submissions cannot beat the limit.
* Idempotency: re-posting the same title and body while an identical contribution is
  still open returns that contribution (``created=False``) without a second post,
  notification or rate-limit charge.
* Reviewer notifications and the ``blog.contribution_submitted`` event are sent after
  commit; a failed delivery never rolls back the submission.
* Terminal failures are :class:`ContributionError` (limit, closed, invalid state) and
  carry a message that is safe to show.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from functools import partial
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.blog.access import (
    blog_enabled_for,
    blog_settings,
    can_author_posts,
    is_approved_seller,
)
from apps.blog.models import Category, Post, PostAccess, PostStatus
from apps.blog.services import workflow
from apps.blog.services.history import PostHistoryService
from apps.core.editor import sanitize_stored_html
from apps.core.events.bus import event_bus
from apps.core.services.notifications import notification_service

logger = logging.getLogger(__name__)

SUBMITTED_EVENT: Final = "blog.contribution_submitted"
MAX_REVIEWER_NOTIFICATIONS: Final = 20
EDITABLE_STATUSES: Final = frozenset({PostStatus.DRAFT, PostStatus.IN_REVIEW})


class ContributionError(ValueError):
    """A business rule refused the action; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class ContributionData:
    """What the member wrote and the access type they chose."""

    title: str
    body: str
    summary: str = ""
    category: Category | None = None
    tags: Iterable[Any] = ()
    access: str = PostAccess.FREE
    proposed_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """The contribution and whether this call created it (``False``: a duplicate)."""

    post: Post
    created: bool


def is_verified(user: Any) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "email_verified_at", None) is not None
    )


def is_open_to(user: Any) -> bool:
    """Whether the member may contribute now: blog on, member posting allowed."""
    return bool(is_verified(user) and blog_enabled_for(user) and can_author_posts(user))


def _require_open(user: Any) -> None:
    if not is_verified(user):
        raise PermissionDenied("Verify your email address before contributing.")
    if not blog_enabled_for(user) or not can_author_posts(user):
        raise ContributionError("Blog contributions are closed right now.")


def _review_staff() -> list[Any]:
    codename = "change_post"
    return list(
        get_user_model()
        .objects.filter(is_active=True, is_staff=True)
        .filter(
            Q(is_superuser=True)
            | Q(user_permissions__codename=codename)
            | Q(groups__permissions__codename=codename)
        )
        .distinct()[:MAX_REVIEWER_NOTIFICATIONS]
    )


def _notify(user_id: int, title: str, message: str, url: str) -> None:
    notification_service.send(user_id, title, message, "info", url=url)


def _after_commit_announce(post: Post, *, resubmitted: bool) -> None:
    """Queue reviewer notifications and the bus event for after the commit."""
    admin_url = reverse("admin:blog_post_change", args=[post.pk])
    verb = "resubmitted" if resubmitted else "submitted"
    for reviewer in _review_staff():
        transaction.on_commit(
            partial(
                _notify,
                reviewer.pk,
                "Blog contribution to review",
                f"“{post.title}” was {verb} by {post.author} and is waiting for review.",
                admin_url,
            )
        )
    payload = {"post_id": post.pk, "author_id": post.author_id}
    transaction.on_commit(partial(event_bus.publish, SUBMITTED_EVENT, payload))


def _lock_member(user: Any) -> None:
    """Serialise one member's submissions (no-op row lock on SQLite)."""
    list(get_user_model().objects.select_for_update().filter(pk=user.pk))


def resolve_access(user: Any, data: ContributionData) -> tuple[str, Decimal | None]:
    """The access type and proposed price a member may ask for (authoritative).

    Free and Featured are open to every contributor, with no price. Paid needs an
    approved marketplace seller and a proposed price above zero.
    """
    access = data.access or PostAccess.FREE
    if access not in PostAccess.values:
        raise ContributionError("Unsupported access type.")
    if access != PostAccess.PAID:
        return access, None
    if not is_approved_seller(user):
        raise ContributionError("Only approved sellers can offer paid posts.")
    price = data.proposed_price
    if price is None or price <= 0:
        raise ContributionError("A paid post needs a proposed price above zero.")
    return access, price.quantize(Decimal("0.01"))


def contributions_today(user: Any) -> int:
    since = timezone.now() - timedelta(days=1)
    return Post.objects.filter(
        author=user, is_community_contribution=True, created_at__gte=since
    ).count()


def remaining_today(user: Any) -> int:
    return max(blog_settings().contributions_per_day - contributions_today(user), 0)


def _existing_duplicate(user: Any, data: ContributionData) -> Post | None:
    cleaned = sanitize_stored_html(data.body)
    candidates = Post.objects.filter(
        author=user,
        is_community_contribution=True,
        status=PostStatus.IN_REVIEW,
        title=data.title.strip(),
    )
    return next((post for post in candidates if post.body == cleaned), None)


def submit(user: Any, data: ContributionData) -> SubmitResult:
    """Create a contribution in review, or return the identical open one."""
    _require_open(user)
    title = data.title.strip()
    if not title or not data.body.strip():
        raise ContributionError("A contribution needs a title and a body.")
    access, price = resolve_access(user, data)
    with transaction.atomic():
        _lock_member(user)
        duplicate = _existing_duplicate(user, data)
        if duplicate is not None:
            return SubmitResult(duplicate, created=False)
        limit = blog_settings().contributions_per_day
        if contributions_today(user) >= limit:
            raise ContributionError(
                f"You can contribute {limit} posts per day. Try again tomorrow."
            )
        post = Post(
            title=title,
            summary=data.summary.strip(),
            body=data.body,
            author=user,
            category=data.category,
            access=access,
            proposed_price=price,
            status=PostStatus.DRAFT,
            is_community_contribution=True,
        )
        try:
            post.full_clean(exclude=["slug", "author"])
        except ValidationError as error:
            raise ContributionError(" ".join(error.messages)) from error
        post.save()
        post.tags.set(list(data.tags))
        PostHistoryService.record_current_version(
            post, user=user, change_summary="Initial contribution"
        )
        workflow.request_review(post, user=user)
        _after_commit_announce(post, resubmitted=False)
    return SubmitResult(post, created=True)


def _own_contribution(user: Any, post: Post) -> None:
    if not is_verified(user) or post.author_id != user.pk:
        raise PermissionDenied("Only the author can change a contribution.")
    if not post.is_community_contribution:
        raise PermissionDenied("This post is not a member contribution.")


def can_edit(user: Any, post: Post) -> bool:
    """The author may edit while it waits for review or after it was sent back."""
    return bool(
        is_verified(user)
        and post.author_id == user.pk
        and post.is_community_contribution
        and post.status in EDITABLE_STATUSES
    )


def can_withdraw(user: Any, post: Post) -> bool:
    return can_edit(user, post) and post.status == PostStatus.IN_REVIEW


def update(user: Any, post: Post, data: ContributionData) -> Post:
    """Edit an open contribution; a draft that was returned goes back to review."""
    _own_contribution(user, post)
    access, price = resolve_access(user, data)
    with transaction.atomic():
        locked = Post.objects.select_for_update().get(pk=post.pk)
        if locked.status not in EDITABLE_STATUSES:
            raise ContributionError(
                "Only a contribution that is waiting for review or was sent back "
                "can be edited."
            )
        was_draft = locked.status == PostStatus.DRAFT
        fields: dict[str, Any] = {
            "title": data.title.strip(),
            "summary": data.summary.strip(),
            "body": data.body,
            "category": data.category,
            "access": access,
            "proposed_price": price,
        }
        if access != PostAccess.PAID:
            fields["price"] = None
        PostHistoryService.update_post(
            locked,
            user=user,
            change_summary="Edited by the author",
            tags=list(data.tags),
            **fields,
        )
        if was_draft:
            workflow.request_review(locked, user=user)
            _after_commit_announce(locked, resubmitted=True)
    post.refresh_from_db()
    return post


def withdraw(user: Any, post: Post) -> Post:
    """The author pulls a contribution out of review (back to their drafts)."""
    _own_contribution(user, post)
    with transaction.atomic():
        locked = Post.objects.select_for_update().get(pk=post.pk)
        try:
            workflow.withdraw(locked, user=user)
        except ValidationError as error:
            raise ContributionError(" ".join(error.messages)) from error
    post.refresh_from_db()
    return post


def reject(user: Any, post: Post, reason: str) -> Post:
    """A moderator sends a contribution back with a reason; the author is told."""
    with transaction.atomic():
        locked = Post.objects.select_for_update().get(pk=post.pk)
        workflow.reject(locked, user=user, reason=reason)
        transaction.on_commit(partial(notify_author_returned, locked))
    post.refresh_from_db()
    return post


def lower_access(user: Any, post: Post, access: str, reason: str) -> Post:
    """A moderator lowers the access type with a reason; the author is told."""
    with transaction.atomic():
        locked = Post.objects.select_for_update().get(pk=post.pk)
        workflow.lower_access(locked, user=user, access=access, reason=reason)
        transaction.on_commit(partial(notify_author_access_lowered, locked))
    post.refresh_from_db()
    return post


def notify_author_access_lowered(post: Post) -> None:
    """Tell the author the reviewer changed the access type they asked for."""
    if not post.is_community_contribution:
        return
    _notify(
        post.author_id,
        "The access type of your blog contribution changed",
        f"“{post.title}”: {post.review_note}",
        reverse("blog:contribute_edit", kwargs={"slug": post.slug}),
    )


def notify_author_published(post: Post) -> None:
    """Tell the author their contribution went live (called once per publication)."""
    if not post.is_community_contribution:
        return
    _notify(
        post.author_id,
        "Your blog contribution was published",
        f"“{post.title}” passed review and is live.",
        post.get_absolute_url(),
    )


def notify_author_returned(post: Post) -> None:
    """Tell the author a reviewer sent the contribution back, with the reason."""
    if not post.is_community_contribution:
        return
    _notify(
        post.author_id,
        "Your blog contribution needs changes",
        f"“{post.title}”: {post.review_note}",
        reverse("blog:contribute_edit", kwargs={"slug": post.slug}),
    )
