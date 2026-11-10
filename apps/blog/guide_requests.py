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
from dataclasses import dataclass, replace
from datetime import timedelta
from difflib import SequenceMatcher
from functools import partial
from typing import Any, Final

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.blog.access import blog_settings
from apps.blog.models import GuideRequest, GuideRequestFollower, Post
from apps.core.editor.sanitizer import sanitize_stored_html, text_length
from apps.core.events.bus import event_bus
from apps.core.services.notifications import notification_service

logger = logging.getLogger(__name__)

APPROVED_EVENT: Final = "blog.guide_request_approved"
CLOSED_EVENT: Final = "blog.guide_request_closed"
FULFILLED_EVENT: Final = "blog.guide_fulfilled"

SIMILARITY_THRESHOLD: Final = 0.82
TOPIC_OVERLAP: Final = 0.4
MIN_TRIED_LENGTH: Final = 20
MAX_TEXT_LENGTH: Final = 2000
MAX_BOUNTY_OFFER: Final = 100_000
FOLLOW_MILESTONES: Final = frozenset({1, 5, 10, 25, 50, 100})
MAX_FOLLOWER_NOTIFICATIONS: Final = 200
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


@dataclass(frozen=True, slots=True)
class RequestText:
    """What the member wrote: the ask, what was tried and the wanted outcome."""

    title: str
    kind: str
    tried: str
    description: str = ""
    expected_outcome: str = ""
    search_keywords: str = ""


@dataclass(frozen=True, slots=True)
class RequestContext:
    """The device and article the request is about."""

    brand: Any = None
    firmware_model: Any = None
    variant: Any = None
    chipset: Any = None
    model_code: str = ""
    variant_code: str = ""
    related_post: Post | None = None


class DuplicateRequestError(GuideRequestError):
    """Open public requests already cover the topic; the member may follow one."""

    def __init__(self, duplicates: list[GuideRequest]) -> None:
        super().__init__(
            "Similar requests are already open: follow one of them, or confirm "
            "that yours is different."
        )
        self.duplicates = duplicates


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
    title: str,
    *,
    brand: Any = None,
    firmware_model: Any = None,
    exclude_post_id: int | None = None,
) -> list[Duplicate]:
    """Live posts and public requests with a near-identical title.

    Pending requests of other members are never listed (they are private); they
    still block a duplicate, reported without a link by :func:`create_request`.
    ``exclude_post_id`` is the article the member says did not help.
    """
    found: list[Duplicate] = []
    posts = Post.objects.live()
    if exclude_post_id is not None:
        posts = posts.exclude(pk=exclude_post_id)
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


def find_open_duplicates(
    title: str, context: RequestContext, kind: str
) -> list[GuideRequest]:
    """Open public requests for the same model or variant on the same topic.

    The topic matches when the kind is the same or the titles are alike. Pending
    requests of other members are private and never listed.
    """
    device = _device_q(context)
    if not device:
        return []
    rows = (
        GuideRequest.objects.filter(status=Status.APPROVED)
        .filter(device)
        .select_related("brand", "firmware_model", "variant", "chipset")[
            :CANDIDATE_LIMIT
        ]
    )
    return [
        row
        for row in rows
        if row.kind == kind or _topic_overlap(title, row.title) >= TOPIC_OVERLAP
    ][:5]


def _topic_overlap(left: str, right: str) -> float:
    a, b = set(_tokens(left)), set(_tokens(right))
    return len(a & b) / len(a | b) if a and b else 0.0


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
    notification_service.send(user_id, title, message, "info")


def _publish(event: str, payload: dict[str, Any]) -> None:
    transaction.on_commit(lambda: event_bus.publish(event, payload))


def _device_q(context: RequestContext) -> Q:
    """Rows describing the same device: model, variant or typed codes."""
    device = Q()
    if context.firmware_model is not None:
        device |= Q(firmware_model=context.firmware_model)
    if context.variant is not None:
        device |= Q(variant=context.variant)
    if context.model_code.strip():
        device |= Q(model_code__iexact=context.model_code.strip())
    if context.variant_code.strip():
        device |= Q(variant_code__iexact=context.variant_code.strip())
    return device


def normalise_context(context: RequestContext) -> RequestContext:
    """Link the model, brand and variant consistently; a device code is required."""
    model, variant, brand = context.firmware_model, context.variant, context.brand
    if variant is not None:
        if model is not None and variant.model_id != model.pk:
            raise GuideRequestError("That variant belongs to a different model.")
        model = model or variant.model
    if model is not None:
        if brand is not None and model.brand_id != brand.pk:
            raise GuideRequestError("That model belongs to a different brand.")
        brand = brand or model.brand
    model_code = context.model_code.strip()[:64]
    variant_code = context.variant_code.strip()[:64]
    if not (model or variant or model_code or variant_code):
        raise GuideRequestError("Enter the model code or the variant code.")
    related = context.related_post
    if related is not None and not related.is_live:
        raise GuideRequestError("That article is not available any more.")
    return replace(
        context,
        brand=brand,
        firmware_model=model,
        variant=variant,
        model_code=model_code,
        variant_code=variant_code,
    )


def _check_text(text: RequestText, bounty_offer: int | None) -> RequestText:
    if text.kind not in GuideRequest.Kind.values:
        raise GuideRequestError("Choose what kind of help you need.")
    tried = sanitize_stored_html(text.tried)
    description = sanitize_stored_html(text.description)
    if text_length(tried) < MIN_TRIED_LENGTH:
        raise GuideRequestError(
            f"Describe what you already tried (at least {MIN_TRIED_LENGTH} characters)."
        )
    for label, value in (("What you tried", tried), ("The extra details", description)):
        if text_length(value) > MAX_TEXT_LENGTH:
            raise GuideRequestError(f"{label} must not exceed {MAX_TEXT_LENGTH} characters.")
    if bounty_offer is not None and not 1 <= bounty_offer <= MAX_BOUNTY_OFFER:
        raise GuideRequestError(
            f"A bounty offer is between 1 and {MAX_BOUNTY_OFFER} credits."
        )
    return replace(text, tried=tried, description=description)


def _enforce_limits(user: Any) -> None:
    """Per-day and open-request throttles; call with the member row locked."""
    config = blog_settings()
    recent = GuideRequest.objects.filter(
        requester=user, created_at__gte=timezone.now() - timedelta(days=1)
    ).count()
    if recent >= config.guide_requests_per_day:
        raise GuideRequestError(
            f"You can send {config.guide_requests_per_day} requests per day. "
            "Try again tomorrow."
        )
    active = GuideRequest.objects.filter(
        requester=user, status__in=(Status.PENDING, Status.APPROVED)
    ).count()
    if active >= config.guide_requests_open_limit:
        raise GuideRequestError(
            f"You already have {active} open requests. "
            "Wait for a decision or cancel one."
        )


def _check_duplicates(
    user: Any, text: RequestText, context: RequestContext, *, allow_duplicates: bool
) -> None:
    related_id = context.related_post.pk if context.related_post else None
    existing = find_duplicates(
        text.title,
        brand=context.brand,
        firmware_model=context.firmware_model,
        exclude_post_id=related_id,
    )
    if existing:
        raise GuideRequestError(
            "This topic is already covered: " + "; ".join(d.title for d in existing)
        )
    pending = (
        GuideRequest.objects.filter(status=Status.PENDING)
        .filter(_device_q(context))
        .exclude(requester=user)
        .only("title")[:CANDIDATE_LIMIT]
    )
    if any(_similar(text.title, other.title) for other in pending):
        raise GuideRequestError(
            "Someone already asked for this and staff are reviewing it."
        )
    if not allow_duplicates:
        open_ones = find_open_duplicates(text.title, context, text.kind)
        if open_ones:
            raise DuplicateRequestError(open_ones)


def create_request(
    user: Any,
    text: RequestText,
    context: RequestContext,
    *,
    bounty_offer: int | None = None,
    allow_duplicates: bool = False,
) -> GuideRequest:
    """File a pending request after the account, throttle and duplicate checks.

    ``tried`` and a model or variant code are mandatory. Open public requests for
    the same device and topic raise :class:`DuplicateRequestError` (the member can
    follow one instead) unless ``allow_duplicates`` confirms the request differs.
    """
    if not blog_settings().allow_guide_requests:
        raise GuideRequestError("Guide requests are switched off right now.")
    if not getattr(user, "is_authenticated", False) or not _verified(user):
        raise GuideRequestError("Verify your email address before requesting a guide.")
    text = _check_text(text, bounty_offer)
    context = normalise_context(context)
    with transaction.atomic():
        # Serialise one member's requests so concurrent posts cannot beat the limits.
        list(get_user_model().objects.select_for_update().filter(pk=user.pk))
        _enforce_limits(user)
        _check_duplicates(user, text, context, allow_duplicates=allow_duplicates)
        request = GuideRequest.objects.create(
            requester=user,
            title=text.title.strip()[:200],
            kind=text.kind,
            tried=text.tried.strip(),
            description=text.description.strip(),
            expected_outcome=text.expected_outcome.strip()[:300],
            search_keywords=text.search_keywords.strip()[:200],
            bounty_offer=bounty_offer,
            brand=context.brand,
            firmware_model=context.firmware_model,
            variant=context.variant,
            chipset=context.chipset,
            model_code=context.model_code,
            variant_code=context.variant_code,
            related_post=context.related_post,
        )
        for reviewer in _staff_with_review_permission():
            _notify_on_commit(
                reviewer.pk,
                "Guide request to review",
                f"{request.title} is waiting in the review queue.",
            )
    return request


def follow(request_id: int, user: Any) -> tuple[GuideRequestFollower, bool]:
    """A member also needs this answer ("me too" / bump). Idempotent.

    Only an open public request can be followed, never by its own requester. The
    requester is told when the number of followers reaches a milestone.
    """
    if not getattr(user, "is_authenticated", False) or not _verified(user):
        raise GuideRequestError("Verify your email address to follow a request.")
    with transaction.atomic():
        row = _locked(request_id)
        if row.status != Status.APPROVED:
            raise GuideRequestError("Only open public requests can be followed.")
        if row.requester_id == user.pk:
            raise GuideRequestError("You asked for this yourself.")
        try:
            with transaction.atomic():
                link, created = GuideRequestFollower.objects.get_or_create(
                    request=row, user=user
                )
        except IntegrityError:
            link, created = row.followers.get(user=user), False
        count = row.followers.count()
    if created and count in FOLLOW_MILESTONES:
        _notify(
            row.requester_id,
            "Others need your guide too",
            f"{count} member{'s' if count != 1 else ''} also need “{row.title}”.",
        )
    return link, created


def unfollow(request_id: int, user: Any) -> bool:
    """Stop following a request; ``True`` when a follow was removed."""
    removed, _ = GuideRequestFollower.objects.filter(
        request_id=request_id, user_id=user.pk
    ).delete()
    return bool(removed)


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
    offer = (
        f" Add your {row.bounty_offer}-credit bounty from the request page."
        if row.bounty_offer
        else " You can add a credit bounty to attract a writer."
    )
    _notify(
        row.requester_id,
        "Your guide request was approved",
        f"“{row.title}” is public.{offer}",
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
    follower_ids = row.followers.values_list("user_id", flat=True)
    for follower_id in follower_ids[:MAX_FOLLOWER_NOTIFICATIONS]:
        _notify(
            follower_id,
            "A request you follow was answered",
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
    """Public URL of a request."""
    return reverse("blog:request_detail", kwargs={"pk": request.pk})
