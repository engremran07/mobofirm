"""Promotional package prices: what a user pays while a discount is live."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from .audience import audience_matches, is_authenticated
from .grants import cap_check, cap_check_total_only, live_promotions, record_redemption
from .models import Promotion, PromotionKind

if TYPE_CHECKING:
    from apps.shop.models import PackageTier


@dataclass(frozen=True)
class PriceQuote:
    """The promotional price of a package for one user; ``None`` fields keep the regular one."""

    promotion_id: int
    promotion_name: str
    offer_url: str
    ends_at: datetime | None
    regular_price: Decimal
    regular_credit_price: int
    price: Decimal
    credit_price: int

    @property
    def has_discount(self) -> bool:
        return self.price < self.regular_price or (
            self.credit_price < self.regular_credit_price
        )


def _usable(user: Any, promotion: Promotion) -> bool:
    if not audience_matches(user, promotion):
        return False
    if is_authenticated(user):
        return cap_check(user, promotion).ok
    return cap_check_total_only(promotion).ok


def _discounted_credits(
    promotion: Promotion, package: PackageTier, price: Decimal
) -> int:
    """Credits for the promotional price: explicit, else derived with the one rate.

    A package with a fixed credit-price override scales that override by the same
    discount ratio instead, so the promotion never ignores the package's own setting.
    """
    if promotion.discounted_credit_price is not None:
        return int(promotion.discounted_credit_price)
    regular = package.credit_price
    if promotion.discounted_price is None or not regular:
        return regular
    if package.credit_price_override and package.price > 0:
        scaled = (Decimal(regular) * price / package.price).to_integral_value(
            ROUND_HALF_UP
        )
        return max(int(scaled), 1)
    return package.credits_for(price)


def _quote(promotion: Promotion, package: PackageTier) -> PriceQuote:
    price = (
        promotion.discounted_price
        if promotion.discounted_price is not None
        else package.price
    )
    return PriceQuote(
        promotion_id=promotion.pk,
        promotion_name=promotion.name,
        offer_url=promotion.get_absolute_url(),
        ends_at=promotion.ends_at,
        regular_price=package.price,
        regular_credit_price=package.credit_price,
        price=price,
        credit_price=_discounted_credits(promotion, package, price),
    )


def best_promotion(user: Any, package: PackageTier) -> Promotion | None:
    best_rank: tuple[Decimal, int] | None = None
    best: Promotion | None = None
    candidates = live_promotions(PromotionKind.DISCOUNTED_PACKAGE).filter(
        target_package_id=package.pk
    )
    for promotion in candidates:
        if not _usable(user, promotion):
            continue
        quote = _quote(promotion, package)
        rank = (quote.price, quote.credit_price or 0)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = promotion
    return best


def effective_price(user: Any, package: PackageTier) -> PriceQuote | None:
    """The best live discount for ``package`` that applies to ``user``, if any."""
    promotion = best_promotion(user, package)
    if promotion is None:
        return None
    quote = _quote(promotion, package)
    return quote if quote.has_discount else None


def redeem(user: Any, package: PackageTier, *, reference: str) -> bool:
    """Record a purchase at the promotional price (once per ``reference``)."""
    if not is_authenticated(user):
        return False
    promotion = best_promotion(user, package)
    if promotion is None:
        return False
    return record_redemption(user, promotion, reference)
