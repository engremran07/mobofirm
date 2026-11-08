"""Earn contract: rules, caps, anti-abuse, modifiers and idempotency."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.wallet import registry, services
from apps.wallet.models import LedgerEntry, RewardRefusal, RewardRule, Wallet
from apps.wallet.services import IdempotencyConflict, WalletError

pytestmark = pytest.mark.django_db

EVENT = "test.contribution"


def grant(user: Any, key: str, **kwargs: Any) -> LedgerEntry | None:
    return services.grant_reward(user, EVENT, key=key, **kwargs)


def test_a_registered_event_pays_its_rule_amount(user: Any, rule: RewardRule) -> None:
    entry = grant(user, "r:1", reference="obj:1")

    assert entry is not None
    assert (entry.amount, entry.event_key, entry.reason) == (10, EVENT, "reward")
    assert entry.snapshot["rule_credits"] == 10
    assert services.balance(user) == 10


def test_rule_rows_are_created_from_the_registered_defaults(db: Any) -> None:
    assert not RewardRule.objects.filter(event_key=EVENT).exists()

    services.sync_registry()
    rule = RewardRule.objects.get(event_key=EVENT)
    rule.credits = 99
    rule.save()
    services.sync_registry()

    assert RewardRule.objects.get(event_key=EVENT).credits == 99


def test_a_reward_is_idempotent_on_its_key(user: Any, rule: RewardRule) -> None:
    first = grant(user, "r:dup")
    again = grant(user, "r:dup")

    assert again is not None and first is not None
    assert again.pk == first.pk
    assert services.balance(user) == 10


def test_a_key_cannot_be_reused_for_another_user(
    user: Any, other: Any, rule: RewardRule
) -> None:
    grant(user, "r:shared")

    with pytest.raises(IdempotencyConflict):
        grant(other, "r:shared")


def test_unregistered_events_are_a_programming_error(user: Any) -> None:
    with pytest.raises(WalletError):
        services.grant_reward(user, "nobody.registered", key="r:x")


def test_a_disabled_rule_refuses_without_raising_and_records_why(
    user: Any, rule: RewardRule
) -> None:
    rule.enabled = False
    rule.save()

    assert grant(user, "r:off") is None
    refusal = RewardRefusal.objects.get(key="r:off")
    assert (refusal.reason, refusal.user_id, refusal.event_key) == (
        "rule_disabled",
        user.pk,
        EVENT,
    )
    assert services.balance(user) == 0


def test_unverified_email_is_refused_unless_the_rule_allows_it(
    make_user: Any, rule: RewardRule
) -> None:
    unverified = make_user("unverified", verified=False)

    assert grant(unverified, "r:u1") is None
    assert RewardRefusal.objects.get(key="r:u1").reason == "email_unverified"

    rule.requires_verified_email = False
    rule.save()
    assert grant(unverified, "r:u2") is not None


def test_new_accounts_wait_for_the_minimum_age(user: Any, rule: RewardRule) -> None:
    rule.min_account_age_days = 3
    rule.save()

    assert grant(user, "r:age") is None
    assert RewardRefusal.objects.get(key="r:age").reason == "account_too_new"

    user.date_joined = timezone.now() - timedelta(days=4)
    user.save()
    assert grant(user, "r:age2") is not None


def test_self_rewards_are_refused(user: Any, rule: RewardRule) -> None:
    assert grant(user, "r:self", context={"source_user_id": user.pk}) is None
    assert RewardRefusal.objects.get(key="r:self").reason == "self_reward"


def test_an_object_pays_only_once_even_with_a_new_key(
    user: Any, other: Any, rule: RewardRule
) -> None:
    assert grant(user, "r:o1", reference="firmware:7") is not None
    assert grant(other, "r:o2", reference="firmware:7") is None
    assert RewardRefusal.objects.get(key="r:o2").reason == "duplicate_object"

    rule.per_object_once = False
    rule.save()
    assert grant(other, "r:o3", reference="firmware:7") is not None


def test_daily_monthly_and_lifetime_caps_limit_earnings(
    user: Any, rule: RewardRule
) -> None:
    rule.daily_cap = 25
    rule.save()
    assert grant(user, "c:1") is not None
    assert grant(user, "c:2") is not None
    assert grant(user, "c:3") is None
    assert RewardRefusal.objects.get(key="c:3").reason == "daily_cap"

    rule.daily_cap = 0
    rule.lifetime_cap = 30
    rule.save()
    assert grant(user, "c:4") is not None
    assert grant(user, "c:5") is None
    assert RewardRefusal.objects.get(key="c:5").reason == "lifetime_cap"

    rule.lifetime_cap = 0
    rule.monthly_cap = 30
    rule.save()
    assert grant(user, "c:6") is None
    assert RewardRefusal.objects.get(key="c:6").reason == "monthly_cap"


def test_cooldown_spaces_out_rewards(user: Any, rule: RewardRule) -> None:
    rule.cooldown_seconds = 3600
    rule.save()

    assert grant(user, "cd:1") is not None
    assert grant(user, "cd:2") is None
    assert RewardRefusal.objects.get(key="cd:2").reason == "cooldown"


def test_a_frozen_wallet_earns_nothing(user: Any, staff: Any, rule: RewardRule) -> None:
    services.set_frozen(user, True, actor=staff, reason_text="Review")

    assert grant(user, "f:1") is None
    assert RewardRefusal.objects.get(key="f:1").reason == "wallet_frozen"


def test_the_caller_can_supply_the_amount_and_caps_still_apply(
    user: Any, rule: RewardRule
) -> None:
    entry = grant(user, "amt:1", amount_override=7)
    assert entry is not None and entry.amount == 7

    rule.lifetime_cap = 10
    rule.save()
    assert grant(user, "amt:2", amount_override=5) is None


def test_zero_credit_rewards_are_refused_not_booked(
    user: Any, rule: RewardRule
) -> None:
    rule.credits = 0
    rule.save()

    assert grant(user, "z:1") is None
    assert RewardRefusal.objects.get(key="z:1").reason == "zero_credits"
    assert not LedgerEntry.objects.exists()


def test_reward_modifiers_adjust_the_amount_and_are_recorded(
    user: Any, rule: RewardRule
) -> None:
    def double(_user: Any, event_key: str, credits: int, _ctx: Any) -> int:
        return credits * 2 if event_key == EVENT else credits

    registry.register_reward_modifier(double)
    try:
        entry = grant(user, "m:1")
    finally:
        registry._reward_modifiers.remove(double)

    assert entry is not None and entry.amount == 20
    assert entry.snapshot["base"] == 10
    assert any("double" in item for item in entry.snapshot["modifiers"])


def test_conflicting_event_registration_is_rejected() -> None:
    clash = registry.RewardEvent(key=EVENT, label="Other", app="x", default_credits=1)

    with pytest.raises(ValueError, match="already registered"):
        registry.register_reward_event(clash)


def test_rewards_publish_a_credited_event_after_commit(
    user: Any, rule: RewardRule, django_capture_on_commit_callbacks: Any
) -> None:
    from apps.core.events.bus import EventTypes, event_bus

    seen: list[Any] = []
    event_bus.subscribe(EventTypes.WALLET_CREDITED, seen.append)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            grant(user, "ev:1")
    finally:
        event_bus.unsubscribe(EventTypes.WALLET_CREDITED, seen.append)

    assert seen and seen[0]["event_key"] == EVENT and seen[0]["amount"] == 10
    assert Wallet.objects.get(user=user).balance == 10
