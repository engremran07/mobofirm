"""Reward boosts through the wallet modifier and promotional package prices."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.promotions import api
from apps.promotions.models import Promotion, PromotionGrant, PromotionKind
from apps.shop import credits as shop_credits
from apps.shop import services as shop_services
from apps.shop.models import Order, UserPackage
from apps.wallet import api as wallet_api
from apps.wallet.models import LedgerEntry, RewardRefusal, RewardRule

EVENT = "test.promo_event"


def pay(user: Any, key: str, **kwargs: Any) -> LedgerEntry | None:
    return wallet_api.grant_reward(user, EVENT, key=key, **kwargs)


def test_a_multiplier_raises_the_reward_and_is_recorded_on_the_ledger(
    make_promotion: Any, user: Any, django_capture_on_commit_callbacks: Any
) -> None:
    promotion = make_promotion(PromotionKind.REWARD_MULTIPLIER)

    with django_capture_on_commit_callbacks(execute=True):
        entry = pay(user, "k1")

    promotion.refresh_from_db()
    assert entry is not None and entry.amount == 20
    assert entry.snapshot["base"] == 10
    assert any("reward_modifier:10->20" in text for text in entry.snapshot["modifiers"])
    assert (promotion.credits_granted, promotion.conversion_count) == (10, 1)


def test_a_replayed_reward_is_not_boosted_or_counted_twice(
    make_promotion: Any, user: Any, django_capture_on_commit_callbacks: Any
) -> None:
    promotion = make_promotion(PromotionKind.REWARD_MULTIPLIER)

    with django_capture_on_commit_callbacks(execute=True):
        first = pay(user, "k1")
        second = pay(user, "k1")

    promotion.refresh_from_db()
    assert first is not None and second is not None and first.pk == second.pk
    assert wallet_api.balance(user) == 20
    assert promotion.conversion_count == 1


def test_a_flat_bonus_is_added_after_the_multiplier(
    make_promotion: Any, user: Any
) -> None:
    make_promotion(
        PromotionKind.REWARD_MULTIPLIER,
        reward_multiplier=Decimal("1.50"),
        bonus_credits=3,
    )

    entry = pay(user, "k1")

    assert entry is not None and entry.amount == 18


def test_the_best_multiplier_wins_and_multipliers_do_not_stack(
    make_promotion: Any, user: Any
) -> None:
    make_promotion(PromotionKind.REWARD_MULTIPLIER)
    make_promotion(PromotionKind.REWARD_MULTIPLIER, reward_multiplier=Decimal("3.00"))

    entry = pay(user, "k1")

    assert entry is not None and entry.amount == 30


def test_other_events_audiences_and_inactive_promotions_are_not_boosted(
    make_promotion: Any, user: Any, make_user: Any
) -> None:
    make_promotion(PromotionKind.REWARD_MULTIPLIER, audience="sellers")
    make_promotion(PromotionKind.REWARD_MULTIPLIER, live=False)
    make_promotion(PromotionKind.REWARD_MULTIPLIER, event_keys=["profile.completed"])

    entry = pay(user, "k1")

    assert entry is not None and entry.amount == 10


def test_the_budget_limits_the_extra_credits(make_promotion: Any, user: Any) -> None:
    promotion = make_promotion(PromotionKind.REWARD_MULTIPLIER, budget_credits=4)

    first = pay(user, "k1")
    promotion.refresh_from_db()
    assert first is not None and first.amount == 14


def test_a_refused_reward_leaves_the_counters_alone(
    make_promotion: Any, user: Any, django_capture_on_commit_callbacks: Any
) -> None:
    promotion = make_promotion(PromotionKind.REWARD_MULTIPLIER)
    RewardRule.objects.filter(event_key=EVENT).update(enabled=False)

    with django_capture_on_commit_callbacks(execute=True):
        entry = pay(user, "k1")

    promotion.refresh_from_db()
    assert entry is None
    assert RewardRefusal.objects.filter(key="k1").exists()
    assert (promotion.credits_granted, promotion.conversion_count) == (0, 0)


def test_a_referral_boost_defaults_to_the_referral_reward_event(
    make_promotion: Any, user: Any
) -> None:
    promotion = make_promotion(
        PromotionKind.REFERRAL_BOOST, reward_multiplier=Decimal("2.00")
    )

    entry = wallet_api.grant_reward(user, "referral.verified", key="r1", reference="r1")

    assert promotion.boosted_events == ("referral.verified",)
    assert entry is not None and entry.amount == 40


def test_the_promotion_bonus_event_is_never_boosted_itself(
    make_promotion: Any, user: Any
) -> None:
    make_promotion(PromotionKind.REWARD_MULTIPLIER)
    claim_promotion = make_promotion(PromotionKind.CREDITS_BONUS, bonus_credits=7)

    from apps.promotions.grants import claim

    claim(user, claim_promotion)

    assert wallet_api.balance(user) == 7


# ---------------------------------------------------------------- discounts


@pytest.fixture
def discount(make_promotion: Any, premium: Any) -> Promotion:
    return make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        name="Launch price",
        target_package=premium,
        discounted_price=Decimal("5.00"),
        discounted_credit_price=60,
    )


def test_a_live_discount_quotes_the_promotional_price(
    discount: Promotion, premium: Any, user: Any
) -> None:
    quote = api.effective_price(user, premium)

    assert quote is not None
    assert (quote.price, quote.credit_price) == (Decimal("5.00"), 60)
    assert (quote.regular_price, quote.regular_credit_price) == (Decimal("10.00"), 100)
    assert quote.offer_url == f"/offers/{discount.slug}/"


def test_no_discount_without_a_live_matching_promotion(
    premium: Any, user: Any, make_promotion: Any
) -> None:
    assert api.effective_price(user, premium) is None
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        live=False,
        target_package=premium,
        discounted_price=Decimal("5.00"),
    )
    assert api.effective_price(user, premium) is None


def test_orders_use_the_promotional_price_and_record_the_redemption(
    discount: Promotion, premium: Any, user: Any
) -> None:
    order = shop_services.create_order(user, premium)

    discount.refresh_from_db()
    item = order.items.get()
    assert (order.total, item.unit_price, item.line_total) == (Decimal("5.00"),) * 3
    grant = PromotionGrant.objects.get(user=user, promotion=discount)
    assert grant.trigger == f"order:{order.pk}"
    assert (discount.claim_count, discount.conversion_count) == (1, 1)


def test_requesting_the_same_order_again_redeems_only_once(
    discount: Promotion, premium: Any, user: Any
) -> None:
    first = shop_services.create_order(user, premium)
    again = shop_services.create_order(user, premium)

    assert first.pk == again.pk
    assert PromotionGrant.objects.filter(promotion=discount).count() == 1


def test_the_per_user_cap_ends_the_discount_for_that_user_only(
    discount: Promotion, premium: Any, user: Any, other: Any
) -> None:
    shop_services.create_order(user, premium)

    assert api.effective_price(user, premium) is None
    assert api.effective_price(other, premium) is not None


def test_the_total_cap_ends_the_discount_for_everyone(
    make_promotion: Any, premium: Any, user: Any, other: Any
) -> None:
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("5.00"),
        max_claims_total=1,
    )

    shop_services.create_order(user, premium)

    assert api.effective_price(other, premium) is None


def test_discount_audiences_apply(
    make_promotion: Any, premium: Any, make_user: Any
) -> None:
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("5.00"),
        audience="verified_email",
    )

    assert api.effective_price(make_user("v"), premium) is not None
    assert api.effective_price(make_user("u", verified=False), premium) is None
    assert api.effective_price(AnonymousUser(), premium) is None


def test_anonymous_visitors_see_a_discount_for_everyone(
    discount: Promotion, premium: Any
) -> None:
    quote = api.effective_price(AnonymousUser(), premium)

    assert quote is not None and quote.price == Decimal("5.00")


def test_paying_with_credits_charges_the_promotional_credit_price(
    discount: Promotion, premium: Any, user: Any
) -> None:
    wallet_api.credit(user, 100, reason=wallet_api.LedgerReason.TOPUP, key="top")

    order = shop_credits.pay_package_with_credits(user, premium)

    assert order.credits_spent == 60 and order.status == Order.Status.PAID
    assert wallet_api.balance(user) == 40
    assert UserPackage.objects.get(user=user, status="active").package_id == premium.pk
    assert PromotionGrant.objects.filter(
        promotion=discount, trigger=f"order:{order.pk}"
    ).exists()


def test_credits_below_the_promotional_price_are_refused_and_nothing_is_recorded(
    discount: Promotion, premium: Any, user: Any
) -> None:
    wallet_api.credit(user, 50, reason=wallet_api.LedgerReason.TOPUP, key="top")

    with pytest.raises(shop_credits.InsufficientCreditsError):
        shop_credits.pay_package_with_credits(user, premium)

    assert PromotionGrant.objects.count() == 0
    assert Order.objects.count() == 0


def test_the_pricing_page_shows_the_promotional_price_and_offer(
    discount: Promotion, premium: Any, client: Any
) -> None:
    html = client.get(reverse("shop:pricing")).content.decode()

    assert "$5/mo" in html and "Launch price" in html
    assert f"/offers/{discount.slug}/" in html
    assert "<s " in html


def test_the_pricing_page_is_unchanged_without_a_promotion(
    premium: Any, client: Any
) -> None:
    html = client.get(reverse("shop:pricing")).content.decode()

    assert "$10/mo" in html and "Launch price" not in html
