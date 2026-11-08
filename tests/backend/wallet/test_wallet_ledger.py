"""Ledger invariants: idempotency, atomicity, append-only, freeze, validation."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.db.models import QuerySet

from apps.wallet import services
from apps.wallet.models import LedgerEntry, LedgerReason, Wallet
from apps.wallet.services import (
    IdempotencyConflict,
    InsufficientCredits,
    InvalidAmount,
    WalletFrozen,
)

pytestmark = pytest.mark.django_db


def test_credit_and_debit_keep_a_running_balance(user: Any) -> None:
    first = services.credit(user, 100, reason=LedgerReason.TOPUP, key="k:1")
    second = services.debit(user, 30, reason=LedgerReason.SPEND, key="k:2")

    assert (first.balance_after, second.balance_after) == (100, 70)
    assert services.balance(user) == 70
    audit = services.audit_wallet(Wallet.objects.get(user=user))
    assert audit.consistent
    assert audit.entries == 2


def test_balance_of_a_user_without_wallet_is_zero_and_creates_nothing(
    user: Any,
) -> None:
    assert services.balance(user) == 0
    assert not Wallet.objects.filter(user=user).exists()


def test_repeating_a_key_returns_the_original_entry_and_changes_nothing(
    user: Any,
) -> None:
    first = services.credit(user, 50, reason=LedgerReason.TOPUP, key="dup")
    again = services.credit(user, 50, reason=LedgerReason.TOPUP, key="dup")

    assert again.pk == first.pk
    assert services.balance(user) == 50
    assert LedgerEntry.objects.filter(key="dup").count() == 1


def test_a_key_reused_for_another_amount_or_direction_is_a_conflict(
    user: Any, other: Any
) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="dup")

    with pytest.raises(IdempotencyConflict):
        services.credit(user, 60, reason=LedgerReason.TOPUP, key="dup")
    with pytest.raises(IdempotencyConflict):
        services.debit(user, 50, reason=LedgerReason.SPEND, key="dup")
    with pytest.raises(IdempotencyConflict):
        services.credit(other, 50, reason=LedgerReason.TOPUP, key="dup")
    assert services.balance(user) == 50


def test_lost_race_on_the_unique_key_is_a_conflict_and_leaves_the_balance(
    user: Any,
) -> None:
    services.credit(user, 10, reason=LedgerReason.TOPUP, key="seed")

    with (
        patch.object(
            LedgerEntry.objects, "create", side_effect=IntegrityError("duplicate")
        ),
        pytest.raises(IdempotencyConflict),
    ):
        services.credit(user, 5, reason=LedgerReason.TOPUP, key="raced")

    assert services.balance(user) == 10
    assert services.audit_wallet(Wallet.objects.get(user=user)).consistent


def test_debit_beyond_the_balance_raises_and_writes_nothing(user: Any) -> None:
    services.credit(user, 20, reason=LedgerReason.TOPUP, key="fund")

    with pytest.raises(InsufficientCredits) as caught:
        services.debit(user, 21, reason=LedgerReason.SPEND, key="too-much")

    assert (caught.value.needed, caught.value.available) == (21, 20)
    assert services.balance(user) == 20
    assert not LedgerEntry.objects.filter(key="too-much").exists()


@pytest.mark.parametrize("amount", [0, -5, 1.5, True, "10", None])
def test_only_positive_whole_numbers_are_accepted(user: Any, amount: Any) -> None:
    with pytest.raises(InvalidAmount):
        services.credit(user, amount, reason=LedgerReason.TOPUP, key="bad")
    assert not LedgerEntry.objects.exists()


def test_wallet_rows_are_locked_with_select_for_update_of_self(user: Any) -> None:
    with patch.object(
        QuerySet,
        "select_for_update",
        autospec=True,
        side_effect=QuerySet.select_for_update,
    ) as locker:
        services.credit(user, 5, reason=LedgerReason.TOPUP, key="lock")

    assert any(call.kwargs.get("of") == ("self",) for call in locker.call_args_list)


def test_ledger_entries_are_append_only(user: Any) -> None:
    entry = services.credit(user, 5, reason=LedgerReason.TOPUP, key="immutable")

    entry.amount = 500
    with pytest.raises(TypeError):
        entry.save()
    with pytest.raises(TypeError):
        entry.delete()
    with pytest.raises(TypeError):
        LedgerEntry.objects.all().update(amount=1)
    with pytest.raises(TypeError):
        LedgerEntry.objects.all().delete()
    assert LedgerEntry.objects.get(pk=entry.pk).amount == 5


def test_a_frozen_wallet_refuses_user_driven_changes_but_not_staff(
    user: Any, staff: Any
) -> None:
    services.credit(user, 50, reason=LedgerReason.TOPUP, key="fund")
    services.set_frozen(user, True, actor=staff, reason_text="Chargeback review")

    with pytest.raises(WalletFrozen):
        services.credit(user, 5, reason=LedgerReason.TOPUP, key="blocked")
    with pytest.raises(WalletFrozen):
        services.debit(user, 5, reason=LedgerReason.SPEND, key="blocked-2")
    services.adjust(user, -10, actor=staff, reason_text="Correction", key="adj:1")
    assert services.balance(user) == 40

    services.set_frozen(user, False, actor=staff, reason_text="")
    services.credit(user, 5, reason=LedgerReason.TOPUP, key="allowed")
    assert services.balance(user) == 45


def test_adjustments_need_the_permission_and_a_reason(
    user: Any, other: Any, staff: Any
) -> None:
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        services.adjust(user, 10, actor=other, reason_text="please", key="adj:x")
    with pytest.raises(services.WalletError):
        services.adjust(user, 10, actor=staff, reason_text="  ", key="adj:y")
    with pytest.raises(InvalidAmount):
        services.adjust(user, 0, actor=staff, reason_text="nothing", key="adj:z")
    assert services.balance(user) == 0

    entry = services.adjust(user, 25, actor=staff, reason_text="Goodwill", key="adj:ok")
    assert (entry.reason, entry.actor_id, entry.note) == (
        LedgerReason.ADJUSTMENT,
        staff.pk,
        "Goodwill",
    )


def test_to_credits_rounds_half_up_with_the_configured_rate(db: Any) -> None:
    from decimal import Decimal

    assert services.credits_per_currency() == 10
    assert services.to_credits(Decimal(9)) == 90
    assert services.to_credits(Decimal("0.05")) == 1
    assert services.to_credits(Decimal("0.15")) == 2
    assert services.to_credits(Decimal("0.25")) == 3
    assert services.to_credits(Decimal("0.04")) == 1
    assert services.to_credits(Decimal(0)) == 0
    with pytest.raises(InvalidAmount):
        services.to_credits(Decimal(-1))
