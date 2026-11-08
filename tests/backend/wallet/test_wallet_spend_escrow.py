"""Spend contract and escrow."""

from __future__ import annotations

from typing import Any

import pytest

from apps.wallet import registry, services
from apps.wallet.models import (
    CreditPrice,
    EscrowHold,
    LedgerEntry,
    LedgerReason,
    RewardRule,
    Wallet,
)
from apps.wallet.services import (
    EscrowError,
    IdempotencyConflict,
    InsufficientCredits,
    SinkDisabled,
    UnknownSink,
    WalletError,
)

pytestmark = pytest.mark.django_db

SINK = "test.package"
BOUNTY = "test.bounty"


def fund(user: Any, credits: int, key: str = "fund") -> None:
    services.credit(user, credits, reason=LedgerReason.TOPUP, key=key)


def test_price_of_reads_the_admin_price_times_quantity(db: Any) -> None:
    assert services.price_of(SINK) == 40
    assert services.price_of(SINK, quantity=3) == 120


def test_unknown_and_disabled_sinks_are_refused(db: Any) -> None:
    with pytest.raises(UnknownSink):
        services.price_of("nobody.sells")

    services.price_of(SINK)
    CreditPrice.objects.filter(sink_key=SINK).update(enabled=False)
    with pytest.raises(SinkDisabled):
        services.price_of(SINK)


def test_spend_debits_atomically_and_is_idempotent(user: Any) -> None:
    fund(user, 100)

    entry = services.spend(user, SINK, key="shop:package:1", reference="pkg:1")
    again = services.spend(user, SINK, key="shop:package:1", reference="pkg:1")

    assert again.pk == entry.pk
    assert (entry.amount, entry.event_key, entry.reason) == (40, SINK, "spend")
    assert services.balance(user) == 60


def test_spend_beyond_the_balance_raises_and_books_nothing(user: Any) -> None:
    fund(user, 30)

    with pytest.raises(InsufficientCredits) as caught:
        services.spend(user, SINK, key="shop:package:2")

    assert (caught.value.needed, caught.value.available) == (40, 30)
    assert services.balance(user) == 30
    assert not LedgerEntry.objects.filter(key="shop:package:2").exists()


def test_an_explicit_credit_amount_overrides_the_sink_price(user: Any) -> None:
    fund(user, 100)

    entry = services.spend(user, SINK, key="shop:file:1", price_override=15)

    assert entry.amount == 15
    with pytest.raises(WalletError):
        services.spend(user, SINK, key="shop:file:2", price_override=0)


def test_price_modifiers_apply_a_discount(user: Any) -> None:
    fund(user, 100)

    def half(_sink: str, credits: int, _qty: int, _ctx: Any) -> int:
        return credits // 2

    registry.register_price_modifier(half)
    try:
        assert services.price_of(SINK) == 20
        entry = services.spend(user, SINK, key="shop:disc:1")
    finally:
        registry._price_modifiers.remove(half)

    assert entry.amount == 20


def test_refund_returns_the_credits_once(user: Any) -> None:
    fund(user, 100)
    services.spend(user, SINK, key="shop:package:3")

    first = services.refund("shop:package:3", reason="Order cancelled")
    again = services.refund("shop:package:3", reason="Order cancelled")

    assert again.pk == first.pk
    assert (first.reason, first.amount) == ("refund", 40)
    assert services.balance(user) == 100
    with pytest.raises(WalletError):
        services.refund("never-spent", reason="x")


def test_a_refund_works_even_when_the_wallet_was_frozen_afterwards(
    user: Any, staff: Any
) -> None:
    fund(user, 100)
    services.spend(user, SINK, key="shop:package:4")
    services.set_frozen(user, True, actor=staff, reason_text="Review")

    services.refund("shop:package:4", reason="Cancelled")

    assert services.balance(user) == 100


def test_escrow_reserves_credits_so_they_cannot_be_spent(user: Any) -> None:
    fund(user, 100)

    hold = services.lock_escrow(user, 70, key="bounty:1", reference="bounty:1")

    assert hold.status == EscrowHold.Status.HOLDING
    assert services.balance(user) == 30
    wallet = Wallet.objects.get(user=user)
    assert (wallet.balance, wallet.held) == (100, 70)
    with pytest.raises(InsufficientCredits):
        services.spend(user, SINK, key="shop:blocked")


def test_escrow_needs_available_credits_and_is_idempotent(user: Any) -> None:
    fund(user, 50)

    with pytest.raises(InsufficientCredits):
        services.lock_escrow(user, 60, key="bounty:big")
    first = services.lock_escrow(user, 20, key="bounty:2")
    again = services.lock_escrow(user, 20, key="bounty:2")
    assert again.pk == first.pk
    assert Wallet.objects.get(user=user).held == 20
    with pytest.raises(IdempotencyConflict):
        services.lock_escrow(user, 25, key="bounty:2")


def test_cancelling_escrow_frees_the_credits_once(user: Any, other: Any) -> None:
    fund(user, 50)
    hold = services.lock_escrow(user, 20, key="bounty:3")

    services.cancel_escrow(hold)
    services.cancel_escrow(hold)

    assert services.balance(user) == 50
    assert Wallet.objects.get(user=user).held == 0
    with pytest.raises(EscrowError):
        services.release_escrow(
            hold, to_user=other, key="bounty:3:pay", event_key=BOUNTY
        )


def test_releasing_escrow_pays_the_uploader_through_the_reward_rule(
    user: Any, other: Any
) -> None:
    fund(user, 100)
    hold = services.lock_escrow(user, 60, key="bounty:4", reference="bounty:4")
    services.sync_registry()

    entry = services.release_escrow(
        hold, to_user=other, key="bounty:4:fulfilled", event_key=BOUNTY
    )
    again = services.release_escrow(
        hold, to_user=other, key="bounty:4:fulfilled", event_key=BOUNTY
    )

    assert again.pk == entry.pk
    assert (entry.amount, entry.event_key, entry.reason) == (60, BOUNTY, "reward")
    assert services.balance(other) == 60
    holder = Wallet.objects.get(user=user)
    assert (holder.balance, holder.held) == (40, 0)
    hold.refresh_from_db()
    assert hold.status == EscrowHold.Status.RELEASED
    for wallet in Wallet.objects.all():
        assert services.audit_wallet(wallet).consistent


def test_a_refused_release_rolls_back_and_keeps_the_hold(user: Any, other: Any) -> None:
    fund(user, 100)
    hold = services.lock_escrow(user, 60, key="bounty:5")
    services.sync_registry()
    RewardRule.objects.filter(event_key=BOUNTY).update(enabled=False)

    with pytest.raises(EscrowError):
        services.release_escrow(
            hold, to_user=other, key="bounty:5:pay", event_key=BOUNTY
        )

    hold.refresh_from_db()
    assert hold.status == EscrowHold.Status.HOLDING
    assert services.balance(other) == 0
    assert Wallet.objects.get(user=user).balance == 100


def test_the_holder_cannot_release_escrow_to_themselves(user: Any) -> None:
    fund(user, 100)
    hold = services.lock_escrow(user, 60, key="bounty:6")
    services.sync_registry()

    with pytest.raises(EscrowError):
        services.release_escrow(
            hold, to_user=user, key="bounty:6:pay", event_key=BOUNTY
        )

    assert Wallet.objects.get(user=user).held == 60
