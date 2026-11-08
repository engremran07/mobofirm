"""Guide requests: members ask for a method, pattern, trick or guide; staff gate them.

State machine (every transition is atomic, row-locked and idempotent)::

    pending -> approved | rejected | cancelled
    approved -> fulfilled | cancelled
    rejected, fulfilled, cancelled are terminal

A request is private to its requester and staff until approved. Only an approved
request is public and can carry a credit bounty (``apps.bounty`` reaches it through
the app registry; blog never imports bounty). Events on the bus:

* ``blog.guide_request_approved`` / ``blog.guide_request_closed`` (rejected, cancelled)
* ``blog.guide_fulfilled`` - the solution was published; bounty pays the escrow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from functools import partial
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.blog.access import blog_settings
from apps.blog.models import GuideRequest, Post
from apps.core.events.bus import event_bus

logger = logging.getLogger(__name__)

APPROVED_EVENT: Final = "blog.guide_request_approved"
CLOSED_EVENT: Final = "blog.guide_request_closed"
FULFILLED_EVENT: Final = "blog.guide_fulfilled"

SIMILARITY_THRESHOLD: Final = 0.82
CANDIDATE_LIMIT: Final = 300
MAX_REVIEWER_NOTIFICATIONS: Final = 20
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS: Final = frozenset(
    {"a", "an", "the", "how", "to", "for", "on", "in", "of", "and", "with", "my"}
)

Status = GuideRequest.Status


class GuideRequestError(ValueError):
    """A business rule refused the action; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class Duplicate:
    """An existing post or request that already covers the asked topic."""

    kind: str
    title: str
    url: str


def _verified(user: Any) -> bool:
    return getattr(user, "email_verified_at", None) is not None


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _similar(left: str, right: str) -> bool:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    if SequenceMatcher(None, " ".join(a), " ".join(b)).ratio() >= SIMILARITY_THRESHOLD:
        return True
    shared = len(set(a) & set(b))
    return shared / len(set(a) | set(b)) >= 0.75


def find_duplicates(
    title: str, *, brand: Any = None, firmware_model: Any = None
) -> list[Duplicate]:
    """Live posts and public requests with a near-identical title.

    Pending requests of other members are never listed (they are private); they
    still block a duplicate, reported without a link by :func:`create_request`.
    """
    found: list[Duplicate] = []
    posts = Post.objects.live()
    scope = Q()
    if firmware_model is not None:
        scope |= Q(firmware_model=firmware_model)
    if brand is not None:
        scope |= Q(firmware_brand=brand)
    for token in sorted(set(_tokens(title)), key=len, reverse=True)[:3]:
        scope |= Q(title__icontains=token)
    if scope:
        for post in posts.filter(scope).only("title", "slug")[:CANDIDATE_LIMIT]:
            if _similar(title, post.title):
                found.append(Duplicate("post", post.title, post.get_absolute_url()))
    for other in GuideRequest.objects.filter(status__in=GuideRequest.PUBLIC).only(
        "title", "id"
    )[:CANDIDATE_LIMIT]:
        if _similar(title, other.title):
            found.append(Duplicate("request", other.title, other.get_absolute_url()))
    return found[:5]


def _staff_with_review_permission() -> list[Any]:
    codename = "change_guiderequest"
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


def _notify(user_id: int | None, title: str, message: str) -> None:
    if user_id is None:
        return
    from apps.core.services.notifications import notification_service

    notification_service.send(user_id, title, message, "info")


def _publish(event: str, payload: dict[str, Any]) -> None:
    transaction.on_commit(lambda: event_bus.publish(event, payload))


def create_request(
    user: Any,
    *,
    title: str,
    description: str,
    kind: str,
    brand: Any = None,
    firmware_model: Any = None,
    chipset: Any = None,
) -> GuideRequest:
    """File a pending request after the account, throttle and duplicate checks."""
    config = blog_settings()
    if not config.allow_guide_requests:
        raise GuideRequestError("Guide requests are switched off right now.")
    if not getattr(user, "is_authenticated", False) or not _verified(user):
        raise GuideRequestError("Verify your email address before requesting a guide.")
    if kind not in GuideRequest.Kind.values:
        raise GuideRequestError("Choose what kind of help you need.")
    if firmware_model is not None and brand is None:
        brand = firmware_model.brand
    if (
        firmware_model is not None
        and brand is not None
        and firmware_model.brand_id != brand.pk
    ):
        raise GuideRequestError("That model belongs to a different brand.")
    with transaction.atomic():
        # Serialise one member's requests so concurrent posts cannot beat the limits.
        list(get_user_model().objects.select_for_update().filter(pk=user.pk))
        recent = GuideRequest.objects.filter(
            requester=user, created_at__gte=timezone.now() - timedelta(days=1)
        ).count()
        if recent >= config.guide_requests_per_day:
            raise GuideRequestError(
                f"You can send {config.guide_requests_per_day} requests per day. Try again tomorrow."
            )
        active = GuideRequest.objects.filter(
            requester=user, status__in=(Status.PENDING, Status.APPROVED)
        ).count()
        if active >= config.guide_requests_open_limit:
            raise GuideRequestError(
                f"You already have {active} open requests. Wait for a decision or cancel one."
            )
        existing = find_duplicates(title, brand=brand, firmware_model=firmware_model)
        if existing:
            raise GuideRequestError(
                "This topic is already covered: " + "; ".join(d.title for d in existing)
            )
        if any(
            _similar(title, other.title)
            for other in GuideRequest.objects.filter(status=Status.PENDING)
            .exclude(requester=user)
            .only("title")[:CANDIDATE_LIMIT]
        ):
            raise GuideRequestError(
                "Someone already asked for this and staff are reviewing it."
            )
        request = GuideRequest.objects.create(
            requester=user,
            title=title.strip()[:200],
            description=description.strip(),
            kind=kind,
            brand=brand,
            firmware_model=firmware_model,
            chipset=chipset,
        )
        for reviewer in _staff_with_review_permission():
            _notify_on_commit(
                reviewer.pk,
                "Guide request to review",
                f"{request.title} is waiting in the review queue.",
            )
    return request


def _notify_on_commit(user_id: int, title: str, message: str) -> None:
    transaction.on_commit(partial(_notify, user_id, title, message))


def _locked(request_id: int) -> GuideRequest:
    return GuideRequest.objects.select_for_update().get(pk=request_id)


def _require_reviewer(staff: Any) -> None:
    if not (
        getattr(staff, "is_active", False)
        and getattr(staff, "is_staff", False)
        and staff.has_perm("blog.change_guiderequest")
    ):
        raise GuideRequestError("Only staff with review permission can decide.")


def approve(request_id: int, *, reviewer: Any, note: str = "") -> GuideRequest:
    """Make a pending request public and bounty-eligible. Idempotent."""
    _require_reviewer(reviewer)
    with transaction.atomic():
        row = _locked(request_id)
        if row.status == Status.APPROVED:
            return row
        if row.status != Status.PENDING:
            raise GuideRequestError(
                f"A {row.get_status_display().lower()} request cannot be approved."
            )
        row.status = Status.APPROVED
        row.reviewed_by = reviewer
        row.reviewed_at = timezone.now()
        row.decision_reason = note.strip()[:500]
        row.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "decision_reason",
                "updated_at",
            ]
        )
        _publish(APPROVED_EVENT, {"request_id": row.pk})
    _notify(
        row.requester_id,
        "Your guide request was approved",
        f"“{row.title}” is public. You can add a credit bounty to attract a writer.",
    )
    return row


def reject(request_id: int, *, reviewer: Any, reason: str) -> GuideRequest:
    """Refuse a pending request with a reason the requester can read. Idempotent."""
    _require_reviewer(reviewer)
    text = reason.strip()
    if not text:
        raise GuideRequestError("Give the requester a reason.")
    with transaction.atomic():
        row = _locked(request_id)
        if row.status == Status.REJECTED:
            return row
        if row.status != Status.PENDING:
            raise GuideRequestError(
                f"A {row.get_status_display().lower()} request cannot be rejected."
            )
        row.status = Status.REJECTED
        row.reviewed_by = reviewer
        row.reviewed_at = timezone.now()
        row.decision_reason = text[:500]
        row.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "decision_reason",
                "updated_at",
            ]
        )
        _publish(CLOSED_EVENT, {"request_id": row.pk, "status": row.status})
    _notify(
        row.requester_id,
        "Your guide request was not approved",
        f"“{row.title}”: {row.decision_reason}",
    )
    return row


def cancel(request_id: int, *, actor: Any, reason: str = "Cancelled") -> GuideRequest:
    """The requester or staff withdraws a pending or approved request. Idempotent.

    Bounties already attached are refunded by ``apps.bounty`` on the closed event.
    """
    with transaction.atomic():
        row = _locked(request_id)
        allowed = actor.pk == row.requester_id or (
            getattr(actor, "is_staff", False)
            and actor.has_perm("blog.change_guiderequest")
        )
        if not allowed:
            raise GuideRequestError("Only the requester or staff can cancel a request.")
        if row.status == Status.CANCELLED:
            return row
        if row.status not in (Status.PENDING, Status.APPROVED):
            raise GuideRequestError(
                f"A {row.get_status_display().lower()} request cannot be cancelled."
            )
        row.status = Status.CANCELLED
        row.decision_reason = (reason.strip() or "Cancelled")[:500]
        row.save(update_fields=["status", "decision_reason", "updated_at"])
        _publish(CLOSED_EVENT, {"request_id": row.pk, "status": row.status})
    return row


def accepts_solution(user: Any, request: GuideRequest) -> bool:
    """Any verified member except the requester may answer an approved request."""
    return (
        request.accepts_solutions
        and getattr(user, "is_authenticated", False)
        and _verified(user)
        and user.pk != request.requester_id
    )


def on_post_published(post: Post) -> GuideRequest | None:
    """Mark the request a published solution answers as fulfilled (idempotent).

    Called after the publication commits. When the request is not approved any more
    (cancelled, already fulfilled by another post) the post is simply published.
    """
    if post.guide_request_id is None:
        return None
    with transaction.atomic():
        row = _locked(post.guide_request_id)
        if row.status != Status.APPROVED:
            return row
        row.status = Status.FULFILLED
        row.fulfilled_by_post = post
        row.fulfilled_at = timezone.now()
        row.save(
            update_fields=["status", "fulfilled_by_post", "fulfilled_at", "updated_at"]
        )
        _publish(
            FULFILLED_EVENT,
            {
                "request_id": row.pk,
                "post_id": post.pk,
                "author": post.author,
                "author_id": post.author_id,
                "access": post.access,
                "title": row.title,
            },
        )
    _notify(
        row.requester_id,
        "Your guide request was answered",
        f"“{row.title}” now has a published solution: {post.title}.",
    )
    return row


def reconcile_fulfilments() -> int:
    """Retry fulfilment for live solutions whose request is still approved.

    Covers a crash between publishing and fulfilling; safe to run repeatedly.
    """
    done = 0
    stuck = (
        Post.objects.live()
        .filter(guide_request__status=Status.APPROVED)
        .select_related("author")
    )
    for post in stuck:
        if on_post_published(post) is not None:
            done += 1
    return done


def request_url(request: GuideRequest) -> str:
    return reverse("blog:request_detail", kwargs={"pk": request.pk})
