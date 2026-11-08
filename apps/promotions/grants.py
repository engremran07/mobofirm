"""Granting what a promotion promises: claims, earn progress and discount redemptions.

Every grant is idempotent per ``(user, promotion, trigger)`` (a database constraint),
verified-email only, capped per promotion/user/budget and audited in the promotion log.
A promotion never keeps balances: credits go through the wallet, packages through the
shop service and bonus quota through the entitlement modifier in ``effects``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.core.events.bus import DomainError, event_bus

from . import gateways
from .audience import audience_matches, is_authenticated, is_verified
from .models import (
    CLAIMABLE_KINDS,
    ENTITLEMENT_KINDS,
    GrantOutcome,
    Promotion,
    PromotionGrant,
    PromotionKind,
    PromotionLog,
    PromotionProgress,
    PromotionStatus,
)
from .rules import CONTRIBUTION_APPROVED, EarnRules

logger = logging.getLogger(__name__)

GRANTED_EVENT: Final = "promotions.granted"
CLAIM_TRIGGER: Final = "claim"
EARN_PREFIX: Final = "earn:"
ORDER_PREFIX: Final = "order:"
UPLOAD_EVENTS: Final = frozenset(
    {CONTRIBUTION_APPROVED, "firmware.approved", "firmware.approved.paid"}
)


class ClaimError(DomainError):
    """A claim or grant was refused for a business reason shown to the user."""

    def __init__(self, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    code: str = ""
    message: str = ""


@dataclass(frozen=True)
class Progress:
    done: int
    needed: int
    window_days: int
    noun: str

    @property
    def complete(self) -> bool:
        return self.done >= self.needed

    @property
    def percent(self) -> int:
        return min(100, round(self.done / self.needed * 100)) if self.needed else 0

    @property
    def text(self) -> str:
        window = f" within {self.window_days} days" if self.window_days else ""
        return f"{min(self.done, self.needed)} of {self.needed} {self.noun}{window}"


def log(
    promotion: Promotion,
    action: str,
    *,
    actor: Any = None,
    user: Any = None,
    detail: str = "",
) -> None:
    PromotionLog.objects.create(
        promotion=promotion,
        actor=actor if is_authenticated(actor) else None,
        user=user if is_authenticated(user) else None,
        action=action[:40],
        detail=detail[:255],
    )


def bump(promotion_id: int, **counters: int) -> None:
    """Atomically add to analytics counters (``page_views=1``)."""
    Promotion.objects.filter(pk=promotion_id).update(
        **{name: F(name) + amount for name, amount in counters.items()}
    )


def lock_promotion(promotion_id: int) -> Promotion:
    return Promotion.objects.select_for_update().get(pk=promotion_id)


def offer_path(promotion: Promotion, source: str = "") -> str:
    path = reverse("promotions:offer", kwargs={"slug": promotion.slug})
    return f"{path}?src={source}" if source else path


def live_promotions(*kinds: str) -> QuerySet[Promotion]:
    """Active promotions inside their window (the status sync may lag a minute)."""
    now = timezone.now()
    live = Promotion.objects.filter(status=PromotionStatus.ACTIVE).exclude(
        starts_at__gt=now
    )
    live = live.exclude(ends_at__lte=now)
    return live.filter(kind__in=kinds) if kinds else live


# ---------------------------------------------------------------- eligibility


def _grants(promotion: Promotion) -> QuerySet[PromotionGrant]:
    return PromotionGrant.objects.filter(
        promotion=promotion, outcome=GrantOutcome.GRANTED
    )


def grants_for_user(user: Any, promotion: Promotion) -> QuerySet[PromotionGrant]:
    return _grants(promotion).filter(user=user)


def cap_check(
    user: Any, promotion: Promotion, *, credit_amount: int = 0
) -> Eligibility:
    """Total, per-user and credit-budget caps, in that order."""
    if promotion.max_claims_total and _grants(promotion).count() >= (
        promotion.max_claims_total
    ):
        return Eligibility(False, "total_cap", "Every place in this offer is taken.")
    if is_authenticated(user) and grants_for_user(user, promotion).count() >= (
        promotion.max_claims_per_user
    ):
        return Eligibility(False, "user_cap", "You have already taken this offer.")
    if (
        promotion.budget_credits
        and promotion.credits_granted + credit_amount > promotion.budget_credits
    ):
        return Eligibility(False, "budget", "This offer's credit budget is used up.")
    return Eligibility(True)


def _base_check(user: Any, promotion: Promotion) -> Eligibility:
    if not promotion.is_live:
        return Eligibility(False, "not_live", "This offer is not running right now.")
    if not is_authenticated(user):
        return Eligibility(False, "anonymous", "Sign in to take this offer.")
    if not is_verified(user):
        return Eligibility(
            False, "unverified", "Verify your email address to take offers."
        )
    if not audience_matches(user, promotion):
        return Eligibility(
            False, "audience", "This offer is not available for your account."
        )
    return Eligibility(True)


def claim_check(user: Any, promotion: Promotion) -> Eligibility:
    """May this user claim the promotion now? (read-only, used by page and dialog)"""
    if promotion.kind not in CLAIMABLE_KINDS:
        return Eligibility(False, "not_claimable", "This offer is applied for you.")
    base = _base_check(user, promotion)
    if not base.ok:
        return base
    return cap_check(user, promotion, credit_amount=_credits_of(promotion))


def _credits_of(promotion: Promotion) -> int:
    if promotion.kind in (
        PromotionKind.CREDITS_BONUS,
        PromotionKind.EARN_BY_CONTRIBUTING,
    ):
        return promotion.bonus_credits
    return 0


# ---------------------------------------------------------------- issuing


def _package_days(promotion: Promotion, now: datetime) -> int:
    if promotion.trial_days:
        return promotion.trial_days
    if promotion.ends_at is None:
        raise ClaimError("This offer has no length set.", "misconfigured")
    return max(1, math.ceil((promotion.ends_at - now).total_seconds() / 86400))


def _grant_package(user: Any, promotion: Promotion, now: datetime) -> datetime | None:
    package = promotion.target_package
    if package is None:
        return None
    held = gateways.live_package(user)
    if held is not None and held.package_id != package.pk:
        raise ClaimError(
            f"You already have the {held.package.name} package; this offer is "
            "for accounts on the free tier.",
            "has_package",
        )
    try:
        user_package = gateways.grant_package(
            user,
            package,
            reference=gateways.package_label(promotion.pk),
            days=_package_days(promotion, now),
        )
    except gateways.GatewayUnavailable as error:
        raise ClaimError(str(error), "unavailable") from error
    return user_package.expires_at


def _credit_key(promotion: Promotion, user: Any, trigger: str) -> str:
    base = f"promotion:{promotion.pk}:{user.pk}"
    return base if trigger == CLAIM_TRIGGER else f"{base}:{trigger}"


def _pay_credits(
    user: Any, promotion: Promotion, trigger: str, credit_amount: int
) -> None:
    try:
        gateways.credit_bonus(
            user,
            credit_amount,
            key=_credit_key(promotion, user, trigger),
            note=f'Campaign "{promotion.name}"'[:255],
        )
    except gateways.GatewayUnavailable as error:
        raise ClaimError(str(error), "unavailable") from error
    except gateways.credits_error_types() as error:
        raise ClaimError(
            "Your wallet cannot receive credits right now.", "wallet"
        ) from error


def _issue(user: Any, promotion: Promotion, trigger: str) -> PromotionGrant:
    """Create the grant and apply its effects; caller holds the promotion lock."""
    now = timezone.now()
    kind = promotion.kind
    expires_at = promotion.grant_expiry(now) if kind in ENTITLEMENT_KINDS else None
    if kind in (PromotionKind.FREE_TIER_GRANT, PromotionKind.EARN_BY_CONTRIBUTING):
        expires_at = _grant_package(user, promotion, now) or expires_at
    bonus = _credits_of(promotion)
    grant = PromotionGrant.objects.create(
        user=user,
        promotion=promotion,
        trigger=trigger,
        credits=bonus,
        detail=promotion.effect_summary()[:255],
        expires_at=expires_at,
    )
    if bonus:
        _pay_credits(user, promotion, trigger, bonus)
    bump(promotion.pk, claim_count=1, credits_granted=bonus)
    log(promotion, "granted", user=user, detail=f"{trigger}: {grant.detail}")
    _announce_grant(user, promotion, trigger)
    return grant


def _announce_grant(user: Any, promotion: Promotion, trigger: str) -> None:
    payload = {
        "user_id": user.pk,
        "promotion_id": promotion.pk,
        "kind": promotion.kind,
        "trigger": trigger,
    }
    url = offer_path(promotion, "notification")
    title = f"{promotion.name}: it's yours"
    message = promotion.effect_summary()

    def after_commit() -> None:
        gateways.notify(user, title, message, url)
        event_bus.publish(GRANTED_EVENT, payload)

    transaction.on_commit(after_commit)


@transaction.atomic
def claim(user: Any, promotion: Promotion) -> PromotionGrant:
    """Claim a claimable offer; repeating the call returns the first grant."""
    locked = lock_promotion(promotion.pk)
    existing = grants_for_user(user, locked).filter(trigger=CLAIM_TRIGGER).first()
    if existing is not None:
        return existing
    check = claim_check(user, locked)
    if not check.ok:
        raise ClaimError(check.message, check.code)
    try:
        with transaction.atomic():
            return _issue(user, locked, CLAIM_TRIGGER)
    except IntegrityError:
        existing = grants_for_user(user, locked).filter(trigger=CLAIM_TRIGGER).first()
        if existing is None:
            raise
        return existing


# ---------------------------------------------------------------- earn by contributing


def _noun(rules: EarnRules) -> str:
    if set(rules.events) <= UPLOAD_EVENTS:
        return "approved uploads"
    return "qualifying rewards"


def _earn_grants(user: Any, promotion: Promotion) -> QuerySet[PromotionGrant]:
    return grants_for_user(user, promotion).filter(trigger__startswith=EARN_PREFIX)


def progress_for(user: Any, promotion: Promotion) -> Progress | None:
    """How far ``user`` is towards the next reward (``None`` without earn rules)."""
    rules = promotion.rules()
    if rules is None or not is_authenticated(user):
        return None
    counted = PromotionProgress.objects.filter(promotion=promotion, user=user)
    last = _earn_grants(user, promotion).order_by("-granted_at").first()
    if last is not None:
        counted = counted.filter(counted_at__gt=last.granted_at)
    if rules.within_days:
        counted = counted.filter(
            counted_at__gte=timezone.now() - timedelta(days=rules.within_days)
        )
    return Progress(counted.count(), rules.count, rules.within_days, _noun(rules))


def earn_blocked_reason(user: Any, promotion: Promotion) -> Eligibility:
    """Whether a completed earn rule may pay out now (caps, audience, verification)."""
    base = _base_check(user, promotion)
    if not base.ok:
        return base
    if _earn_grants(user, promotion).count() >= promotion.max_claims_per_user:
        return Eligibility(False, "user_cap", "You have already earned this reward.")
    return cap_check_total_only(promotion)


def cap_check_total_only(promotion: Promotion) -> Eligibility:
    if promotion.max_claims_total and _grants(promotion).count() >= (
        promotion.max_claims_total
    ):
        return Eligibility(False, "total_cap", "Every place in this offer is taken.")
    if (
        promotion.budget_credits
        and promotion.credits_granted + promotion.bonus_credits
        > promotion.budget_credits
    ):
        return Eligibility(False, "budget", "This offer's credit budget is used up.")
    return Eligibility(True)


@transaction.atomic
def complete_earn(user: Any, promotion: Promotion) -> PromotionGrant | None:
    """Pay the earn reward when the rules are complete; ``None`` while they are not.

    Used by the event handlers and by the offer page's claim button, so a reward that
    was deferred (for instance because the user held another package) can be taken
    later.
    """
    locked = lock_promotion(promotion.pk)
    progress = progress_for(user, locked)
    if progress is None or not progress.complete:
        return None
    blocked = earn_blocked_reason(user, locked)
    if not blocked.ok:
        log(locked, "earn_blocked", user=user, detail=blocked.code)
        return None
    cycle = _earn_grants(user, locked).count() + 1
    try:
        with transaction.atomic():
            return _issue(user, locked, f"{EARN_PREFIX}{cycle}")
    except ClaimError as error:
        log(locked, "earn_deferred", user=user, detail=error.code)
        return None
    except IntegrityError:
        return None


def record_progress(
    user: Any, event_key: str, ref: str, *, reviewer_id: int | None = None
) -> list[PromotionGrant]:
    """Count one event for every live earn promotion that lists ``event_key``.

    Verified email only, audience respected, a contribution the user approved
    themselves never counts, and ``ref`` (approved object or ledger key) counts once.
    """
    if not is_authenticated(user) or not is_verified(user) or not ref:
        return []
    if reviewer_id is not None and reviewer_id == user.pk:
        return []
    granted: list[PromotionGrant] = []
    for promotion in live_promotions(PromotionKind.EARN_BY_CONTRIBUTING):
        rules = promotion.rules()
        if rules is None or event_key not in rules.events:
            continue
        if not audience_matches(user, promotion):
            continue
        _, created = PromotionProgress.objects.get_or_create(
            promotion=promotion,
            user=user,
            ref=ref[:200],
            defaults={"event_key": event_key[:80]},
        )
        if not created:
            continue
        grant = complete_earn(user, promotion)
        if grant is not None:
            granted.append(grant)
    return granted


@transaction.atomic
def claim_earned(user: Any, promotion: Promotion) -> PromotionGrant:
    """Take an earn reward whose rules are complete but that was not paid yet."""
    if promotion.kind != PromotionKind.EARN_BY_CONTRIBUTING:
        raise ClaimError("This offer is not claimed.", "not_claimable")
    progress = progress_for(user, promotion)
    if progress is None or not progress.complete:
        raise ClaimError("Finish the steps first.", "incomplete")
    locked = lock_promotion(promotion.pk)
    blocked = earn_blocked_reason(user, locked)
    if not blocked.ok:
        raise ClaimError(blocked.message, blocked.code)
    cycle = _earn_grants(user, locked).count() + 1
    return _issue(user, locked, f"{EARN_PREFIX}{cycle}")


# ---------------------------------------------------------------- discounts


@transaction.atomic
def record_redemption(user: Any, promotion: Promotion, reference: str) -> bool:
    """Record that ``user`` bought at the promotional price; once per order."""
    locked = lock_promotion(promotion.pk)
    trigger = f"{ORDER_PREFIX}{reference}"[:120]
    if grants_for_user(user, locked).filter(trigger=trigger).exists():
        return False
    PromotionGrant.objects.create(
        user=user,
        promotion=locked,
        trigger=trigger,
        detail=f"Promotional price, order {reference}"[:255],
    )
    bump(locked.pk, claim_count=1, conversion_count=1)
    log(locked, "redeemed", user=user, detail=reference)
    return True


# ---------------------------------------------------------------- analytics


def record_offer_view(promotion: Promotion, source: str) -> None:
    counters = {"page_views": 1}
    if source in ("banner", "notification"):
        counters["announcement_clicks"] = 1
    bump(promotion.pk, **counters)
