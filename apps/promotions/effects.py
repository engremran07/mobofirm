"""How live promotions change what other apps compute.

* :func:`entitlement_modifier` – registered with the shop resolver; adds the live grants
  of a user (bonus files / GB / device slots, and an explanation in ``sources``).
* :func:`reward_modifier` – registered with the wallet; multiplies or tops up rewards of
  the boosted event keys while a promotion is live. It is a pure function: the wallet
  records the change in the ledger row snapshot, and the analytics counters are updated
  from ``wallet.credited`` once the credit really happened.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final

from django.db.models import Q
from django.utils import timezone

from apps.core.app_service import app_enabled

from .audience import audience_matches, is_authenticated
from .grants import bump, grants_for_user, live_promotions
from .models import (
    ENTITLEMENT_KINDS,
    REWARD_KINDS,
    GrantOutcome,
    Promotion,
    PromotionGrant,
)

if TYPE_CHECKING:
    from apps.shop.entitlements import Entitlements

MB_PER_GB: Final = 1024
PROMOTION_EVENT_PREFIX: Final = "promotion."
_pending = threading.local()


def _until(grant: PromotionGrant) -> str:
    return f" until {grant.expires_at:%b %d}" if grant.expires_at else ""


def _bonus_parts(promotion: Promotion) -> list[str]:
    parts: list[str] = []
    if promotion.bonus_files:
        parts.append(f"+{promotion.bonus_files} files")
    if promotion.bonus_size_gb:
        parts.append(f"+{promotion.bonus_size_gb} GB")
    if promotion.bonus_devices:
        parts.append(f"+{promotion.bonus_devices} device slots")
    return parts


def live_grants(user: Any) -> list[PromotionGrant]:
    """Unexpired entitlement grants of ``user`` with their promotion loaded."""
    now = timezone.now()
    return list(
        PromotionGrant.objects.filter(
            user=user,
            outcome=GrantOutcome.GRANTED,
            promotion__kind__in=ENTITLEMENT_KINDS,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("promotion__target_package")
        .order_by("granted_at", "pk")
    )


def entitlement_modifier(user: Any, current: Entitlements) -> Entitlements:
    """Shop modifier: add the user's live promotion grants to the entitlements."""
    from apps.shop import entitlements

    if not is_authenticated(user) or not app_enabled("promotions"):
        return current
    for grant in live_grants(user):
        promotion = grant.promotion
        parts = _bonus_parts(promotion)
        package = promotion.target_package
        held = current.package
        if (
            package is not None
            and held is not None
            and held.package_id == package.pk
            and held.payment_reference == f"promotion:{promotion.pk}"
        ):
            parts.insert(0, f"{package.name} package")
        if not parts:
            continue
        source = f'Campaign "{promotion.name}" {" ".join(parts)}{_until(grant)}'
        current = entitlements.grant(
            current,
            source,
            download_quota=promotion.bonus_files,
            device_limit=promotion.bonus_devices,
        )
        if promotion.bonus_size_gb and current.limits.size_mb >= 0:
            limits = replace(
                current.limits,
                size_mb=current.limits.size_mb + promotion.bonus_size_gb * MB_PER_GB,
            )
            current = replace(current, limits=limits)
    return current


def _half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _budget_left(promotion: Promotion) -> int | None:
    if not promotion.budget_credits:
        return None
    return max(0, promotion.budget_credits - promotion.credits_granted)


def _pending_store() -> dict[tuple[int, str], list[tuple[int, int]]]:
    store: dict[tuple[int, str], list[tuple[int, int]]] | None = getattr(
        _pending, "uplifts", None
    )
    if store is None:
        store = {}
        _pending.uplifts = store
    return store


def reward_uplifts(
    user: Any, event_key: str, amount: int
) -> list[tuple[Promotion, int]]:
    """Extra credits per promotion for a reward: best multiplier, then flat bonuses."""
    if event_key.startswith(PROMOTION_EVENT_PREFIX) or amount <= 0:
        return []
    promotions = [
        promotion
        for promotion in live_promotions(*REWARD_KINDS)
        if event_key in promotion.boosted_events and audience_matches(user, promotion)
    ]
    if not promotions:
        return []
    left: dict[int, int | None] = {p.pk: _budget_left(p) for p in promotions}
    uplifts: dict[int, int] = {}

    def add(promotion: Promotion, wanted: int) -> None:
        room = left[promotion.pk]
        extra = wanted if room is None else min(wanted, room)
        if extra <= 0:
            return
        uplifts[promotion.pk] = uplifts.get(promotion.pk, 0) + extra
        if room is not None:
            left[promotion.pk] = room - extra

    top = max(promotions, key=lambda p: (p.reward_multiplier, -p.pk))
    if top.reward_multiplier > 1:
        add(top, _half_up(amount * top.reward_multiplier) - amount)
    for promotion in promotions:
        if promotion.bonus_credits:
            add(promotion, promotion.bonus_credits)
    by_id = {p.pk: p for p in promotions}
    return [(by_id[pk], extra) for pk, extra in uplifts.items()]


def reward_modifier(
    user: Any, event_key: str, amount: int, _context: Mapping[str, Any]
) -> int:
    """Wallet modifier: the credits to pay for ``event_key`` while a boost is live."""
    if not app_enabled("promotions"):
        return amount
    uplifts = reward_uplifts(user, event_key, amount)
    store = _pending_store()
    key = (user.pk, event_key)
    if not uplifts:
        store.pop(key, None)
        return amount
    store[key] = [(promotion.pk, extra) for promotion, extra in uplifts]
    return amount + sum(extra for _, extra in uplifts)


def settle_uplifts(user_id: int, event_key: str) -> None:
    """After a reward was credited: count the boost for each promotion that applied."""
    for promotion_id, extra in _pending_store().pop((user_id, event_key), []):
        bump(promotion_id, credits_granted=extra, conversion_count=1)


def discard_uplifts(user_id: int, event_key: str) -> None:
    """A boosted reward was refused by a cap: nothing was granted."""
    _pending_store().pop((user_id, event_key), None)


def has_live_grant(user: Any, promotion: Promotion) -> bool:
    return (
        grants_for_user(user, promotion)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .exists()
    )
