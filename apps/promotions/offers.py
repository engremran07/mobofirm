"""Read models for offer pages and panels: what a visitor can do with a promotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from .audience import audience_matches, is_authenticated, is_verified
from .grants import (
    CLAIM_TRIGGER,
    Progress,
    cap_check,
    earn_blocked_reason,
    grants_for_user,
    live_promotions,
    progress_for,
)
from .models import Promotion, PromotionGrant, PromotionKind, PromotionStatus

ENDED_VISIBLE_DAYS = 30


@dataclass(frozen=True)
class OfferState:
    """One visitor's position in one offer.

    ``code`` is one of: ``ended``, ``upcoming``, ``sign_in``, ``unverified``,
    ``not_eligible``, ``sold_out``, ``claimed``, ``available``, ``in_progress``,
    ``ready`` (earn rules complete, reward not taken yet), ``automatic``.
    """

    code: str
    label: str
    message: str
    can_claim: bool = False
    progress: Progress | None = None
    grant: PromotionGrant | None = None


@dataclass(frozen=True)
class OfferCard:
    slug: str
    name: str
    headline: str
    effect: str
    url: str
    ends_at: datetime | None
    state: str
    state_label: str
    progress_text: str


def offer_state(user: Any, promotion: Promotion) -> OfferState:
    """Resolve the state shown on the offer page, the panels and the claim dialog."""
    if promotion.status == PromotionStatus.ENDED:
        return OfferState("ended", "Ended", "This offer has ended.")
    if not promotion.is_live:
        return OfferState("upcoming", "Coming soon", "This offer has not started yet.")
    if not is_authenticated(user):
        return OfferState("sign_in", "Sign in", "Sign in to take this offer.")
    if not audience_matches(user, promotion):
        return OfferState(
            "not_eligible", "Not for your account", "This offer is for another group."
        )
    if not is_verified(user):
        return OfferState(
            "unverified", "Verify email", "Verify your email address to take offers."
        )
    if promotion.kind == PromotionKind.EARN_BY_CONTRIBUTING:
        return _earn_state(user, promotion)
    if promotion.is_claimable:
        return _claim_state(user, promotion)
    return OfferState(
        "automatic", "Applied for you", "Nothing to claim: this applies automatically."
    )


def _claim_state(user: Any, promotion: Promotion) -> OfferState:
    grant = grants_for_user(user, promotion).filter(trigger=CLAIM_TRIGGER).first()
    if grant is not None:
        return OfferState(
            "claimed", "Claimed", "You have taken this offer.", grant=grant
        )
    check = cap_check(user, promotion, credit_amount=promotion.bonus_credits)
    if not check.ok:
        return OfferState("sold_out", "Unavailable", check.message)
    return OfferState(
        "available", "Available", "Claim it with one confirmation.", can_claim=True
    )


def _earn_state(user: Any, promotion: Promotion) -> OfferState:
    progress = progress_for(user, promotion)
    earned = grants_for_user(user, promotion).filter(trigger__startswith="earn:")
    latest = earned.order_by("-granted_at").first()
    if earned.count() >= promotion.max_claims_per_user and latest is not None:
        return OfferState(
            "claimed",
            "Earned",
            "You earned this reward.",
            progress=progress,
            grant=latest,
        )
    if progress is not None and progress.complete:
        blocked = earn_blocked_reason(user, promotion)
        if blocked.ok:
            return OfferState(
                "ready",
                "Reward ready",
                "You finished the steps. Take your reward.",
                can_claim=True,
                progress=progress,
            )
        return OfferState("sold_out", "Unavailable", blocked.message, progress=progress)
    return OfferState(
        "in_progress",
        "In progress",
        "Keep contributing: approved work counts automatically.",
        progress=progress,
    )


def public_promotions() -> QuerySet[Promotion]:
    """Offers visitors may see: running ones and recently ended ones."""
    cutoff = timezone.now() - timedelta(days=ENDED_VISIBLE_DAYS)
    live_ids = live_promotions().values_list("pk", flat=True)
    recent = Promotion.objects.filter(
        status=PromotionStatus.ENDED, ended_at__gte=cutoff
    ).values_list("pk", flat=True)
    return (
        Promotion.objects.filter(pk__in=[*live_ids, *recent])
        .select_related("target_package")
        .order_by("status", "-activated_at", "-created_at")
    )


def card_for(user: Any, promotion: Promotion) -> OfferCard:
    state = offer_state(user, promotion)
    return OfferCard(
        slug=promotion.slug,
        name=promotion.name,
        headline=promotion.headline,
        effect=promotion.effect_summary(),
        url=promotion.get_absolute_url(),
        ends_at=promotion.ends_at,
        state=state.code,
        state_label=state.label,
        progress_text=state.progress.text if state.progress else "",
    )


def offers_for_user(user: Any, *, limit: int = 4) -> list[OfferCard]:
    """Running offers this user can act on or is progressing in (panels, "ways to earn")."""
    cards: list[OfferCard] = []
    for promotion in live_promotions().select_related("target_package"):
        card = card_for(user, promotion)
        if card.state in ("not_eligible", "sold_out", "upcoming", "ended", "claimed"):
            continue
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards
