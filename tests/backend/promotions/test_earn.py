"""Earn-by-contributing: progress from real events, idempotent grants and anti-abuse."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.promotions.grants import (
    ClaimError,
    claim_earned,
    progress_for,
    record_progress,
)
from apps.promotions.models import (
    PromotionGrant,
    PromotionKind,
    PromotionLog,
    PromotionProgress,
)
from apps.shop import services as shop_services
from apps.shop.entitlements import effective_entitlements
from apps.shop.models import PackageTier, UserPackage
from apps.wallet import api as wallet_api

APPROVED = "contribution.approved"


@pytest.fixture
def earn(make_promotion: Any, premium: Any) -> Any:
    return make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        name="Contribute & unlock",
        target_package=premium,
        trial_days=7,
        earn_rules={"events": [APPROVED], "count": 3, "within_days": 30},
    )


def approve(user: Any, reviewer: Any, pending_id: str) -> None:
    event_bus.publish(
        "firmware.approved",
        {"uploader": user, "reviewer": reviewer, "pending_id": pending_id},
    )


def test_approved_uploads_advance_progress_and_the_last_one_grants_the_package(
    earn: Any, user: Any, staff: Any
) -> None:
    approve(user, staff, "p1")
    approve(user, staff, "p2")

    progress = progress_for(user, earn)
    assert progress is not None
    assert progress.text == "2 of 3 approved uploads within 30 days"
    assert not progress.complete and progress.percent == 67
    assert not PromotionGrant.objects.exists()

    approve(user, staff, "p3")

    grant = PromotionGrant.objects.get(user=user, promotion=earn)
    held = UserPackage.objects.get(user=user, status="active")
    assert (grant.trigger, held.source) == ("earn:1", "promotion")
    assert effective_entitlements(user).package_name == "Gold"
    assert any(
        text.startswith('Campaign "Contribute & unlock" Gold package until ')
        for text in effective_entitlements(user).sources
    )


def test_the_same_approval_delivered_twice_counts_once(
    earn: Any, user: Any, staff: Any
) -> None:
    for _ in range(3):
        approve(user, staff, "p1")

    progress = progress_for(user, earn)
    assert progress is not None and progress.done == 1
    assert PromotionProgress.objects.count() == 1


def test_a_contribution_the_user_approved_themselves_never_counts(
    earn: Any, user: Any
) -> None:
    approve(user, user, "p1")

    assert PromotionProgress.objects.count() == 0


def test_unverified_accounts_do_not_progress(
    earn: Any, make_user: Any, staff: Any
) -> None:
    account = make_user("unverified", verified=False)

    approve(account, staff, "p1")

    assert PromotionProgress.objects.count() == 0


def test_uploads_outside_the_audience_do_not_count(
    make_promotion: Any, premium: Any, user: Any, staff: Any
) -> None:
    make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        audience="new_users",
        target_package=premium,
        trial_days=7,
        earn_rules={"events": [APPROVED], "count": 1},
    )

    approve(user, staff, "p1")

    assert PromotionProgress.objects.count() == 0


def test_only_events_listed_in_the_rules_count(earn: Any, user: Any) -> None:
    assert record_progress(user, "some.other.event", "x1") == []

    assert PromotionProgress.objects.count() == 0


def test_progress_outside_the_time_window_expires(
    earn: Any, user: Any, staff: Any
) -> None:
    approve(user, staff, "old")
    PromotionProgress.objects.update(counted_at=timezone.now() - timedelta(days=31))
    approve(user, staff, "new")

    progress = progress_for(user, earn)
    assert progress is not None and progress.done == 1


def test_an_inactive_promotion_collects_no_progress(
    make_promotion: Any, premium: Any, user: Any, staff: Any
) -> None:
    make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        live=False,
        target_package=premium,
        trial_days=7,
        earn_rules={"events": [APPROVED], "count": 1},
    )

    approve(user, staff, "p1")

    assert PromotionProgress.objects.count() == 0


def test_a_reward_is_taken_once_per_user(earn: Any, user: Any, staff: Any) -> None:
    for number in range(6):
        approve(user, staff, f"p{number}")

    assert PromotionGrant.objects.filter(user=user, promotion=earn).count() == 1


def test_a_repeatable_offer_starts_a_new_cycle_after_each_reward(
    make_promotion: Any, premium: Any, user: Any, staff: Any
) -> None:
    repeat = make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        bonus_credits=5,
        max_claims_per_user=2,
        earn_rules={"events": [APPROVED], "count": 2},
        trial_days=7,
    )

    for number in range(5):
        approve(user, staff, f"p{number}")

    triggers = set(
        PromotionGrant.objects.filter(promotion=repeat).values_list(
            "trigger", flat=True
        )
    )
    assert triggers == {"earn:1", "earn:2"}
    assert wallet_api.balance(user) == 10


def test_a_user_holding_another_package_keeps_progress_and_can_take_it_later(
    earn: Any, user: Any, staff: Any
) -> None:
    silver = PackageTier.objects.create(
        name="Silver", slug="silver", price=5, duration_days=30
    )
    held = shop_services.activate_package(user, silver)

    for number in range(3):
        approve(user, staff, f"p{number}")

    assert not PromotionGrant.objects.exists()
    assert PromotionLog.objects.filter(promotion=earn, action="earn_deferred").exists()
    with pytest.raises(ClaimError) as refused:
        claim_earned(user, earn)
    assert refused.value.code == "has_package"

    shop_services.cancel_package(held)
    grant = claim_earned(user, earn)

    assert grant.trigger == "earn:1"
    assert effective_entitlements(user).package_name == "Gold"


def test_claiming_before_the_rules_are_complete_is_refused(
    earn: Any, user: Any, staff: Any
) -> None:
    approve(user, staff, "p1")

    with pytest.raises(ClaimError) as refused:
        claim_earned(user, earn)

    assert refused.value.code == "incomplete"


def test_the_total_cap_stops_further_rewards(
    make_promotion: Any, premium: Any, make_user: Any, staff: Any
) -> None:
    limited = make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        target_package=premium,
        trial_days=7,
        max_claims_total=1,
        earn_rules={"events": [APPROVED], "count": 1},
    )
    first, second = make_user("first"), make_user("second")

    approve(first, staff, "a")
    approve(second, staff, "b")

    assert PromotionGrant.objects.filter(promotion=limited).count() == 1
    assert PromotionGrant.objects.get().user_id == first.pk


def test_wallet_rewards_count_when_the_rules_list_a_reward_event(
    make_promotion: Any,
    user: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    promotion = make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        bonus_credits=30,
        earn_rules={"events": ["test.promo_event"], "count": 2},
        trial_days=1,
    )

    with django_capture_on_commit_callbacks(execute=True):
        wallet_api.grant_reward(user, "test.promo_event", key="w1")
        wallet_api.grant_reward(user, "test.promo_event", key="w1")
        wallet_api.grant_reward(user, "test.promo_event", key="w2")

    assert PromotionGrant.objects.filter(promotion=promotion, user=user).count() == 1
    assert wallet_api.balance(user) == 10 + 10 + 30


def test_a_refused_wallet_reward_does_not_count(
    make_promotion: Any, user: Any, django_capture_on_commit_callbacks: Any
) -> None:
    from apps.wallet.models import RewardRule

    make_promotion(
        PromotionKind.EARN_BY_CONTRIBUTING,
        bonus_credits=5,
        earn_rules={"events": ["test.promo_event"], "count": 1},
        trial_days=1,
    )
    RewardRule.objects.filter(event_key="test.promo_event").update(enabled=False)

    with django_capture_on_commit_callbacks(execute=True):
        wallet_api.grant_reward(user, "test.promo_event", key="w1")

    assert PromotionProgress.objects.count() == 0


def test_generic_contribution_events_need_an_object_reference(
    earn: Any, user: Any
) -> None:
    event_bus.publish(APPROVED, {"user_id": user.pk, "kind": "firmware"})
    event_bus.publish(APPROVED, {"user_id": user.pk, "kind": "blog", "ref": "post-9"})
    event_bus.publish(APPROVED, {"user_id": user.pk, "kind": "blog", "ref": "post-9"})

    refs = list(PromotionProgress.objects.values_list("ref", flat=True))
    assert refs == ["blog:post-9"]
