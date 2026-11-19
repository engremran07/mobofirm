"""Duplicate delivery and lock protocol of the ledger.

Real parallel transactions need row locks, which SQLite does not have; those run in
``test_wallet_postgres.py``. Here the same guarantees are pinned deterministically:
a replayed key returns the stored entry whatever changed since, and every decision is
taken on rows read *after* the lock, in a fixed lock order.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.wallet import services
from apps.wallet.models import (
    CreditPrice,
    LedgerEntry,
    LedgerReason,
    RewardRule,
    Wallet,
)
from apps.wallet.services import (
    IdempotencyConflict,
    InsufficientCredits,
    lock_wallets,
)

pytestmark = pytest.mark.django_db

SINK = "test.package"
EVENT = "test.contribution"


def fund(user: Any, amount: int, key: str = "fund") -> None:
    services.credit(user, amount, reason=LedgerReason.TOPUP, key=key)


def test_a_replayed_spend_returns_the_first_charge_even_after_a_price_change(
    user: Any,
) -> None:
    fund(user, 200)
    first = services.spend(user, SINK, key="shop:package:u:1", reference="pkg:1")
    CreditPrice.objects.filter(sink_key=SINK).update(credits=99)

    again = services.spend(user, SINK, key="shop:package:u:1", reference="pkg:1")
    override = services.spend(
        user, SINK, key="shop:package:u:1", reference="pkg:1", price_override=7
    )

    assert again.pk == override.pk == first.pk
    assert first.amount == 40
    assert services.balance(user) == 160
    assert LedgerEntry.objects.filter(reason=LedgerReason.SPEND).count() == 1


def test_a_replayed_spend_still_answers_when_the_sink_was_switched_off(
    user: Any,
) -> None:
    fund(user, 100)
    first = services.spend(user, SINK, key="shop:package:u:2")
    CreditPrice.objects.filter(sink_key=SINK).update(enabled=False)

    assert services.spend(user, SINK, key="shop:package:u:2").pk == first.pk
    assert services.balance(user) == 60


def test_a_spend_key_cannot_be_reused_for_another_sink_user_or_reason(
    user: Any, other: Any
) -> None:
    fund(user, 100)
    fund(other, 100, key="fund:other")
    services.spend(user, SINK, key="shop:k:1")

    with pytest.raises(IdempotencyConflict):
        services.spend(other, SINK, key="shop:k:1")
    with pytest.raises(IdempotencyConflict):
        services.credit(user, 40, reason=LedgerReason.TOPUP, key="shop:k:1")
    with pytest.raises(IdempotencyConflict):
        services.credit(user, 100, reason=LedgerReason.REFUND, key="fund")
    assert services.balance(user) == 60


def test_a_replayed_reward_returns_the_first_payment_after_the_rule_changed(
    user: Any, rule: RewardRule
) -> None:
    first = services.grant_reward(user, EVENT, key="ads:rewarded:1", reference="v:1")
    rule.enabled = False
    rule.credits = 500
    rule.save()

    again = services.grant_reward(user, EVENT, key="ads:rewarded:1", reference="v:1")

    assert first is not None and again is not None and again.pk == first.pk
    assert services.balance(user) == 10


def test_a_replayed_refund_and_escrow_settlement_do_not_pay_twice(
    user: Any, other: Any
) -> None:
    fund(user, 100)
    services.spend(user, SINK, key="shop:refund:1")
    services.refund("shop:refund:1", reason="Cancelled")
    services.refund("shop:refund:1", reason="Cancelled again")
    services.sync_registry()
    hold = services.lock_escrow(user, 50, key="bounty:dup")
    services.release_escrow(
        hold, to_user=other, key="bounty:dup:pay", event_key="test.bounty"
    )
    services.release_escrow(
        hold, to_user=other, key="bounty:dup:pay", event_key="test.bounty"
    )

    assert services.balance(user) == 50
    assert services.balance(other) == 50
    assert LedgerEntry.objects.filter(reason=LedgerReason.REFUND).count() == 1
    assert LedgerEntry.objects.filter(reason=LedgerReason.ESCROW).count() == 1
    for wallet in Wallet.objects.all():
        assert services.audit_wallet(wallet).consistent


def test_debits_beyond_the_balance_stop_exactly_at_the_balance(user: Any) -> None:
    fund(user, 100)

    outcomes = []
    for number in range(6):
        try:
            services.debit(user, 30, reason=LedgerReason.SPEND, key=f"par:{number}")
        except InsufficientCredits:
            outcomes.append("refused")
        else:
            outcomes.append("paid")

    assert outcomes == ["paid"] * 3 + ["refused"] * 3
    assert services.balance(user) == 10
    assert services.audit_wallet(Wallet.objects.get(user=user)).consistent


def test_the_lock_returns_fresh_rows_so_a_stale_copy_cannot_overdraw(
    user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fund(user, 100)
    stale = Wallet.objects.get(user=user)
    services.debit(user, 90, reason=LedgerReason.SPEND, key="fresh:1")
    monkeypatch.setattr(services, "get_wallet", lambda _user: stale)

    with pytest.raises(InsufficientCredits) as caught:
        services.debit(user, 50, reason=LedgerReason.SPEND, key="fresh:2")

    assert caught.value.available == 10
    assert Wallet.objects.get(user=user).balance == 10


def test_wallets_are_always_locked_in_primary_key_order(user: Any, other: Any) -> None:
    first = services.get_wallet(user)
    second = services.get_wallet(other)
    low, high = sorted((first.pk, second.pk))

    assert list(lock_wallets(high, low, high)) == [low, high]
    assert list(lock_wallets(low, high)) == [low, high]


def test_escrow_released_in_opposite_directions_locks_in_the_same_order(
    user: Any, other: Any
) -> None:
    fund(user, 100)
    fund(other, 100, key="fund:other")
    services.sync_registry()
    forward = services.lock_escrow(user, 20, key="swap:1")
    backward = services.lock_escrow(other, 20, key="swap:2")

    with patch.object(services, "lock_wallets", wraps=lock_wallets) as locker:
        services.release_escrow(
            forward, to_user=other, key="swap:1:pay", event_key="test.bounty"
        )
        services.release_escrow(
            backward, to_user=user, key="swap:2:pay", event_key="test.bounty"
        )

    pairs = [call.args for call in locker.call_args_list if len(call.args) == 2]
    assert len(pairs) == 2
    assert {tuple(sorted(pair)) for pair in pairs} == {tuple(sorted(pairs[0]))}
    assert services.balance(user) == 100
    assert services.balance(other) == 100


def test_only_per_object_rewards_take_the_rule_lock(
    user: Any, other: Any, rule: RewardRule
) -> None:
    with patch.object(services, "lock_rule", wraps=services.lock_rule) as locker:
        services.grant_reward(user, EVENT, key="rl:1", reference="obj:1")
        services.grant_reward(user, EVENT, key="rl:2")
        rule.per_object_once = False
        rule.save()
        services.grant_reward(other, EVENT, key="rl:3", reference="obj:3")

    assert locker.call_count == 1


def test_two_recipients_cannot_both_be_paid_for_the_same_object(
    user: Any, other: Any, rule: RewardRule
) -> None:
    assert rule.per_object_once
    first = services.grant_reward(user, EVENT, key="obj:a", reference="firmware:9")
    second = services.grant_reward(other, EVENT, key="obj:b", reference="firmware:9")

    assert first is not None and second is None
    assert services.balance(other) == 0
    assert LedgerEntry.objects.filter(reference="firmware:9").count() == 1
