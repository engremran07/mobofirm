"""Claims, caps, audiences and the entitlement / wallet effects of a promotion."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.core.models import AppRegistry
from apps.promotions import lifecycle
from apps.promotions.grants import ClaimError, claim, claim_check
from apps.promotions.models import Promotion, PromotionGrant, PromotionKind
from apps.shop import services as shop_services
from apps.shop.entitlements import effective_entitlements
from apps.shop.models import PackageTier, SizeUnit, UserPackage, WindowKind
from apps.users.models import Notification
from apps.wallet import api as wallet_api
from apps.wallet.models import LedgerEntry


def free_quota() -> int:
    """The free tier's file allowance when no default package exists."""
    return effective_entitlements(None).download_quota or 0


def test_a_claim_adds_bonus_files_and_explains_where_they_come_from(
    make_promotion: Any, user: Any
) -> None:
    before = effective_entitlements(user)
    promotion = make_promotion(name="Launch bonus", bonus_files=10, trial_days=30)

    claim(user, promotion)

    after = effective_entitlements(user)
    assert after.download_quota == (before.download_quota or 0) + 10
    assert before.sources == after.sources[: len(before.sources)]
    assert after.sources[-1].startswith('Campaign "Launch bonus" +10 files until ')


def test_claiming_twice_is_idempotent(
    make_promotion: Any, user: Any, django_capture_on_commit_callbacks: Any
) -> None:
    promotion = make_promotion()

    with django_capture_on_commit_callbacks(execute=True):
        first = claim(user, promotion)
        second = claim(user, promotion)

    promotion.refresh_from_db()
    assert first.pk == second.pk
    assert PromotionGrant.objects.filter(user=user, promotion=promotion).count() == 1
    assert promotion.claim_count == 1
    assert (
        Notification.objects.filter(
            recipient=user, title__contains="it's yours"
        ).count()
        == 1
    )


def test_bonus_device_slots_raise_the_device_limit(
    make_promotion: Any, user: Any
) -> None:
    before = effective_entitlements(user).device_limit
    claim(user, make_promotion(PromotionKind.BONUS_DEVICES, bonus_devices=2))

    assert effective_entitlements(user).device_limit == (before or 0) + 2


def test_bonus_size_adds_gigabytes_to_a_size_limited_package(
    make_promotion: Any, user: Any
) -> None:
    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        bandwidth_enabled=True,
        limit_size=1,
        size_unit=SizeUnit.GB,
        window=WindowKind.DAY,
        device_limit=3,
    )
    before = effective_entitlements(user).limits.size_mb
    claim(user, make_promotion(bonus_files=0, bonus_size_gb=2))

    assert effective_entitlements(user).limits.size_mb == before + 2 * 1024


def test_an_expired_grant_stops_adding_quota(make_promotion: Any, user: Any) -> None:
    promotion = make_promotion(bonus_files=10)
    grant = claim(user, promotion)
    boosted = effective_entitlements(user).download_quota

    PromotionGrant.objects.filter(pk=grant.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    assert effective_entitlements(user).download_quota == (boosted or 0) - 10


def test_a_disabled_promotions_app_contributes_nothing(
    make_promotion: Any, user: Any
) -> None:
    before = effective_entitlements(user).download_quota
    claim(user, make_promotion())
    registry = AppRegistry.get_solo()
    registry.promotions_enabled = False
    registry.save()

    entitled = effective_entitlements(user)
    assert entitled.download_quota == before
    assert all("Campaign" not in text for text in entitled.sources)


def test_the_total_cap_closes_the_offer_for_later_claimants(
    make_promotion: Any, make_user: Any
) -> None:
    promotion = make_promotion(max_claims_total=1)
    first, second = make_user("first"), make_user("second")

    claim(first, promotion)

    check = claim_check(second, promotion)
    assert (check.ok, check.code) == (False, "total_cap")
    with pytest.raises(ClaimError) as refused:
        claim(second, promotion)
    assert refused.value.code == "total_cap"
    assert PromotionGrant.objects.filter(promotion=promotion).count() == 1


def test_the_credit_budget_caps_bonus_payouts(
    make_promotion: Any, make_user: Any
) -> None:
    promotion = make_promotion(
        PromotionKind.CREDITS_BONUS, bonus_credits=25, budget_credits=30
    )
    first, second = make_user("first"), make_user("second")

    claim(first, promotion)

    with pytest.raises(ClaimError) as refused:
        claim(second, promotion)
    assert refused.value.code == "budget"
    promotion.refresh_from_db()
    assert promotion.credits_granted == 25


def test_a_credits_bonus_goes_through_the_wallet_once(
    make_promotion: Any, user: Any
) -> None:
    promotion = make_promotion(PromotionKind.CREDITS_BONUS, bonus_credits=25)

    claim(user, promotion)
    claim(user, promotion)

    entry = LedgerEntry.objects.get(key=f"promotion:{promotion.pk}:{user.pk}")
    assert (entry.amount, entry.reason) == (25, "promotion")
    assert wallet_api.balance(user) == 25


def test_a_frozen_wallet_refuses_the_credits_bonus_and_keeps_no_grant(
    make_promotion: Any, user: Any
) -> None:
    from apps.wallet.models import Wallet
    from apps.wallet.services import get_wallet

    wallet = get_wallet(user)
    Wallet.objects.filter(pk=wallet.pk).update(is_frozen=True)
    promotion = make_promotion(PromotionKind.CREDITS_BONUS)

    with pytest.raises(ClaimError) as refused:
        claim(user, promotion)

    assert refused.value.code == "wallet"
    assert PromotionGrant.objects.count() == 0


@pytest.mark.parametrize(
    ("audience", "expected"),
    [
        ("new_users", "audience"),
        ("sellers", "audience"),
        ("package_holders", "audience"),
    ],
)
def test_audiences_exclude_other_accounts(
    make_promotion: Any, user: Any, audience: str, expected: str
) -> None:
    promotion = make_promotion(audience=audience)

    check = claim_check(user, promotion)

    assert (check.ok, check.code) == (False, expected)


def test_new_users_and_package_holders_and_sellers_can_claim(
    make_promotion: Any,
    make_user: Any,
    premium: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = make_user("fresh", days_old=2)
    holder = make_user("holder")
    seller = make_user("seller")
    shop_services.activate_package(holder, premium)
    monkeypatch.setattr(
        "apps.promotions.audience.gateways.is_seller", lambda u: u.pk == seller.pk
    )

    assert claim_check(fresh, make_promotion(audience="new_users")).ok
    assert claim_check(holder, make_promotion(audience="package_holders")).ok
    assert claim_check(seller, make_promotion(audience="sellers")).ok


def test_unverified_accounts_cannot_claim(make_promotion: Any, make_user: Any) -> None:
    promotion = make_promotion()

    check = claim_check(make_user("unverified", verified=False), promotion)

    assert (check.ok, check.code) == (False, "unverified")


def test_anonymous_visitors_and_inactive_promotions_cannot_claim(
    make_promotion: Any, user: Any
) -> None:
    from django.contrib.auth.models import AnonymousUser

    running = make_promotion()
    draft = make_promotion(live=False)

    assert claim_check(AnonymousUser(), running).code == "anonymous"
    assert claim_check(user, draft).code == "not_live"
    with pytest.raises(ClaimError):
        claim(user, draft)


def test_a_free_package_grant_activates_the_package_and_says_so(
    make_promotion: Any, user: Any, premium: Any
) -> None:
    promotion = make_promotion(
        PromotionKind.FREE_TIER_GRANT,
        name="Free Gold week",
        target_package=premium,
        trial_days=7,
    )

    grant = claim(user, promotion)

    held = UserPackage.objects.get(user=user, status="active")
    entitled = effective_entitlements(user)
    assert (held.package_id, held.source) == (premium.pk, "promotion")
    assert grant.expires_at == held.expires_at
    assert held.expires_at is not None
    assert held.expires_at - timezone.now() <= timedelta(days=7, minutes=1)
    assert entitled.package_name == "Gold"
    assert entitled.device_limit == 5
    assert any(
        text.startswith('Campaign "Free Gold week" Gold package until ')
        for text in entitled.sources
    )


def test_a_package_grant_never_replaces_a_different_package(
    make_promotion: Any, user: Any, premium: Any
) -> None:
    other = PackageTier.objects.create(
        name="Silver", slug="silver", price=Decimal("5.00"), duration_days=30
    )
    shop_services.activate_package(user, other)
    promotion = make_promotion(
        PromotionKind.FREE_TIER_GRANT, target_package=premium, trial_days=7
    )

    with pytest.raises(ClaimError) as refused:
        claim(user, promotion)

    assert refused.value.code == "has_package"
    assert UserPackage.objects.get(user=user, status="active").package_id == other.pk
    assert PromotionGrant.objects.count() == 0


def test_ending_a_package_promotion_early_cancels_the_granted_package(
    make_promotion: Any, user: Any, premium: Any
) -> None:
    promotion = make_promotion(
        PromotionKind.FREE_TIER_GRANT,
        target_package=premium,
        trial_days=0,
        ends_at=timezone.now() + timedelta(days=5),
    )
    claim(user, promotion)
    assert UserPackage.objects.filter(user=user, status="active").exists()

    lifecycle.end_now(promotion)

    assert not UserPackage.objects.filter(user=user, status="active").exists()
    assert effective_entitlements(user).package is None


def test_grants_are_per_user_and_never_leak(
    make_promotion: Any, user: Any, other: Any
) -> None:
    claim(user, make_promotion(bonus_files=10))

    assert effective_entitlements(other).download_quota == free_quota()
    assert Promotion.objects.get().claim_count == 1
