"""Promotion model rules and the draft → scheduled → active → ended lifecycle."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.promotions import lifecycle
from apps.promotions.grants import claim
from apps.promotions.models import (
    GrantOutcome,
    Promotion,
    PromotionGrant,
    PromotionKind,
    PromotionLog,
    PromotionStatus,
)
from apps.promotions.rules import EarnRuleError, parse_earn_rules
from apps.users.models import Announcement, Notification, NotificationPreferences


def test_a_new_promotion_is_a_draft_that_is_not_live_and_not_announced(
    make_promotion: Any,
) -> None:
    promotion = make_promotion(live=False)

    assert promotion.status == PromotionStatus.DRAFT
    assert promotion.is_live is False
    assert promotion.announcement_id is None
    assert Announcement.objects.count() == 0


def test_starting_a_promotion_publishes_its_announcement(make_promotion: Any) -> None:
    promotion = make_promotion(headline="Launch week", audience="everyone")

    promotion.refresh_from_db()
    banner = promotion.announcement
    assert promotion.status == PromotionStatus.ACTIVE and promotion.is_live
    assert banner is not None
    assert (banner.title, banner.audience) == ("Launch week", "all")
    assert banner.is_active and banner.is_global
    assert banner.cta_url == f"/offers/{promotion.slug}/?src=banner"
    assert banner.cta_label == promotion.cta_label
    assert banner.active_now()


def test_a_restricted_audience_banner_is_for_signed_in_users(
    make_promotion: Any,
) -> None:
    promotion = make_promotion(audience="verified_email")

    promotion.refresh_from_db()
    assert promotion.announcement is not None
    assert promotion.announcement.audience == "user"


def test_a_promotion_that_does_not_announce_publishes_nothing(
    make_promotion: Any,
) -> None:
    promotion = make_promotion(announce=False)

    assert promotion.status == PromotionStatus.ACTIVE
    assert promotion.announcement_id is None


def test_a_scheduled_promotion_starts_by_itself_when_its_window_opens(
    make_promotion: Any,
) -> None:
    now = timezone.now()
    promotion = make_promotion(live=False, starts_at=now + timedelta(days=1))

    lifecycle.schedule(promotion)
    promotion.refresh_from_db()
    assert promotion.status == PromotionStatus.SCHEDULED
    assert promotion.announcement_id is None

    early = lifecycle.sync_statuses(now=now + timedelta(hours=1))
    promotion.refresh_from_db()
    assert early["activated"] == 0 and promotion.status == PromotionStatus.SCHEDULED

    later = lifecycle.sync_statuses(now=now + timedelta(days=1, minutes=1))
    promotion.refresh_from_db()
    assert later["activated"] == 1
    assert promotion.status == PromotionStatus.ACTIVE
    assert promotion.activated_at is not None
    assert promotion.announcement is not None and promotion.announcement.is_active


def test_an_active_promotion_ends_by_itself_and_its_announcement_expires(
    make_promotion: Any,
) -> None:
    now = timezone.now()
    promotion = make_promotion(ends_at=now + timedelta(hours=1))
    promotion.refresh_from_db()
    banner_id = promotion.announcement_id

    result = lifecycle.sync_statuses(now=now + timedelta(hours=2))

    promotion.refresh_from_db()
    banner = Announcement.objects.get(pk=banner_id)
    assert result["ended"] == 1
    assert promotion.status == PromotionStatus.ENDED and promotion.ended_at is not None
    assert banner.is_active is False
    assert promotion.is_live is False


def test_the_sync_is_idempotent(make_promotion: Any) -> None:
    promotion = make_promotion()

    lifecycle.sync_statuses()
    lifecycle.sync_statuses()

    assert Announcement.objects.count() == 1
    assert (
        PromotionLog.objects.filter(
            promotion=promotion, action="announcement_published"
        ).count()
        == 1
    )


def test_ending_early_expires_grants_without_a_trial_length(
    make_promotion: Any, user: Any
) -> None:
    promotion = make_promotion(
        trial_days=0, ends_at=timezone.now() + timedelta(days=10)
    )
    grant = claim(user, promotion)
    assert grant.expires_at is not None and grant.expires_at > timezone.now()

    ended = lifecycle.end_now(promotion)

    grant.refresh_from_db()
    assert ended.status == PromotionStatus.ENDED
    assert grant.expires_at is not None and grant.expires_at <= timezone.now()
    assert grant.is_live is False


def test_ending_early_keeps_trial_grants(make_promotion: Any, user: Any) -> None:
    promotion = make_promotion(trial_days=30)
    grant = claim(user, promotion)

    lifecycle.end_now(promotion)

    grant.refresh_from_db()
    assert grant.is_live


def test_a_staff_edit_of_a_running_promotion_updates_the_announcement(
    make_promotion: Any,
) -> None:
    promotion = make_promotion(headline="Old")
    promotion.refresh_from_db()

    promotion.headline = "New headline"
    promotion.save()

    assert (
        Announcement.objects.get(pk=promotion.announcement_id).title == "New headline"
    )


def test_status_changes_are_validated_and_audited(make_promotion: Any) -> None:
    draft = make_promotion(live=False, starts_at=timezone.now() + timedelta(days=2))

    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.end_now(draft)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.return_to_draft(draft)

    lifecycle.schedule(draft)
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.schedule(draft)
    lifecycle.return_to_draft(draft)

    actions = list(
        PromotionLog.objects.filter(promotion=draft)
        .order_by("id")
        .values_list("action", flat=True)
    )
    assert "scheduled" in actions and "returned_to_draft" in actions


def test_an_ended_promotion_cannot_be_restarted(make_promotion: Any) -> None:
    promotion = make_promotion()
    lifecycle.end_now(promotion)

    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.activate_now(promotion)


def test_scheduling_requires_a_complete_promotion(make_promotion: Any) -> None:
    incomplete = make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE, live=False, discounted_price=Decimal(5)
    )

    with pytest.raises(ValidationError):
        lifecycle.schedule(incomplete)


def test_a_passed_end_date_blocks_scheduling(make_promotion: Any) -> None:
    promotion = make_promotion(live=False, ends_at=timezone.now() - timedelta(days=1))

    with pytest.raises(lifecycle.LifecycleError):
        lifecycle.schedule(promotion)


def test_duplicating_makes_a_clean_draft_copy(make_promotion: Any, user: Any) -> None:
    promotion = make_promotion()
    claim(user, promotion)
    promotion.refresh_from_db()

    copy = lifecycle.duplicate(promotion)

    assert copy.pk != promotion.pk
    assert copy.status == PromotionStatus.DRAFT
    assert (copy.claim_count, copy.page_views, copy.announcement_id) == (0, 0, None)
    assert copy.name != promotion.name and copy.slug != promotion.slug
    assert PromotionGrant.objects.filter(promotion=copy).count() == 0


@pytest.mark.parametrize(
    ("kind", "fields", "bad_field"),
    [
        (PromotionKind.FREE_TIER_GRANT, {"trial_days": 7}, "target_package"),
        (PromotionKind.BONUS_QUOTA, {"trial_days": 7}, "bonus_files"),
        (PromotionKind.BONUS_DEVICES, {"trial_days": 7}, "bonus_devices"),
        (PromotionKind.CREDITS_BONUS, {}, "bonus_credits"),
        (PromotionKind.REWARD_MULTIPLIER, {}, "reward_multiplier"),
        (PromotionKind.EARN_BY_CONTRIBUTING, {"trial_days": 7}, "earn_rules"),
        (PromotionKind.DISCOUNTED_PACKAGE, {}, "target_package"),
        (PromotionKind.BONUS_QUOTA, {"bonus_files": 5}, "trial_days"),
    ],
)
def test_each_kind_validates_what_it_needs(
    kind: str, fields: dict[str, Any], bad_field: str
) -> None:
    promotion = Promotion(name="X", slug="x", kind=kind, **fields)

    with pytest.raises(ValidationError) as caught:
        promotion.full_clean()

    assert bad_field in caught.value.message_dict


def test_a_discount_must_be_below_the_regular_price(
    make_promotion: Any, premium: Any
) -> None:
    promotion = Promotion(
        name="D",
        slug="d",
        kind=PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("10.00"),
        discounted_credit_price=100,
    )

    with pytest.raises(ValidationError) as caught:
        promotion.full_clean()

    assert {"discounted_price", "discounted_credit_price"} <= set(
        caught.value.message_dict
    )


def test_the_window_must_be_ordered() -> None:
    now = timezone.now()
    promotion = Promotion(
        name="W",
        slug="w",
        kind=PromotionKind.CREDITS_BONUS,
        bonus_credits=5,
        starts_at=now,
        ends_at=now - timedelta(days=1),
    )

    with pytest.raises(ValidationError) as caught:
        promotion.full_clean()

    assert "ends_at" in caught.value.message_dict


def test_the_effect_summary_reads_in_plain_language(
    make_promotion: Any, premium: Any
) -> None:
    quota = make_promotion(bonus_files=10, bonus_size_gb=2, bonus_devices=1)
    boost = make_promotion(PromotionKind.REWARD_MULTIPLIER, bonus_credits=3)

    assert quota.effect_summary() == "+10 files, +2 GB, +1 device slots"
    assert boost.effect_summary() == "2× credits, +3 credits per reward"


def test_earn_rules_accept_the_documented_shape() -> None:
    rules = parse_earn_rules(
        {"events": ["contribution.approved"], "count": 3, "within_days": 30}
    )

    assert (rules.events, rules.count, rules.within_days) == (
        ("contribution.approved",),
        3,
        30,
    )
    assert rules.describe("approved uploads") == "3 approved uploads within 30 days"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"events": [], "count": 1},
        {"events": ["a"], "count": 0},
        {"events": ["a"], "count": True},
        {"events": ["a"], "count": 1, "within_days": 9999},
        {"events": [1], "count": 1},
        {"events": ["a"], "count": 1, "extra": 1},
        {"count": 1},
    ],
)
def test_earn_rules_reject_malformed_json(raw: Any) -> None:
    with pytest.raises(EarnRuleError):
        parse_earn_rules(raw)


def test_the_audience_is_notified_once_in_resumable_batches(
    make_promotion: Any, make_user: Any
) -> None:
    users = [make_user(f"member{n}") for n in range(5)]
    muted = make_user("muted")
    NotificationPreferences.objects.update_or_create(
        user=muted, defaults={"web_system": False}
    )
    promotion = make_promotion(live=False, headline="Big news")

    lifecycle.activate_now(promotion)
    sent = lifecycle.notify_audience(promotion.pk, batch=2)
    again = lifecycle.notify_audience(promotion.pk, batch=2)

    promotion.refresh_from_db()
    notices = Notification.objects.filter(title="Big news")
    assert sent == 6 and again == 0
    assert promotion.notified_at is not None
    assert set(notices.values_list("recipient_id", flat=True)) == {
        account.pk for account in users
    }
    assert all(n.url == f"/offers/{promotion.slug}/?src=notification" for n in notices)


def test_an_ended_promotion_is_never_notified(make_promotion: Any, user: Any) -> None:
    promotion = make_promotion()
    lifecycle.end_now(promotion)

    assert lifecycle.notify_audience(promotion.pk) == 0
    assert Notification.objects.filter(recipient=user).count() == 0


def test_a_claim_grant_is_recorded_as_granted(make_promotion: Any, user: Any) -> None:
    grant = claim(user, make_promotion())

    assert grant.outcome == GrantOutcome.GRANTED
    assert grant.trigger == "claim"
