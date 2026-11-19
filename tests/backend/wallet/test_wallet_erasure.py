"""Account erasure (F-041): personal data goes, the ledger and every balance stay."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from apps.wallet import api, services
from apps.wallet.erasure import (
    ERASED,
    ErasureBlocked,
    ErasureReport,
    anonymise_user,
    erasure_blockers,
)
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    RewardRefusal,
    TopUpRequest,
    Wallet,
)

pytestmark = pytest.mark.django_db


def ledger_snapshot(user: Any) -> list[tuple[Any, ...]]:
    """Everything in the ledger that is accounting data, never personal data."""
    return list(
        LedgerEntry.objects.filter(wallet__user=user)
        .order_by("id")
        .values_list(
            "id", "direction", "reason", "amount", "balance_after", "key", "reference"
        )
    )


def history_with_personal_data(user: Any, staff: Any) -> None:
    """A wallet with every kind of free text a person or a staff member wrote."""
    services.credit(user, 800, reason=LedgerReason.EARNING, key="e:sale")
    services.adjust(
        user, 20, actor=staff, reason_text="Paid via a@b.example", key="e:adjust"
    )
    topup = services.request_topup(user, 100)
    services.confirm_topup(
        topup.pk, staff_user=staff, reference="Receipt of Jane Roe, IBAN DE00"
    )
    payout = services.request_payout(
        user, 600, method="paypal", destination="jane@roe.example"
    )
    services.approve_payout(payout.pk, staff_user=staff)
    services.mark_payout_paid(payout.pk, staff_user=staff, reference="PayPal tx 77")
    RewardRefusal.objects.create(
        user=user,
        event_key="test.contribution",
        key="e:refused",
        reason="rule_disabled",
        detail="jane@roe.example tried twice",
    )
    services.set_frozen(user, True, actor=staff, reason_text="Chargeback by Jane Roe")


def test_erasure_scrubs_personal_data_and_keeps_every_balance(
    user: Any, staff: Any
) -> None:
    history_with_personal_data(user, staff)
    before = ledger_snapshot(user)
    balance = services.balance_snapshot(user).total

    report = anonymise_user(user)

    assert ledger_snapshot(user) == before
    wallet = Wallet.objects.get(user=user)
    assert (wallet.balance, wallet.frozen_reason, wallet.is_frozen) == (
        balance,
        "",
        True,
    )
    assert report.unreconciled_wallets == 0
    assert services.balance_snapshot(user).total == balance
    assert not LedgerEntry.objects.filter(wallet=wallet).exclude(note="").exists()
    # The staff member who acted is the staff member's data, not the erased user's.
    assert LedgerEntry.objects.filter(wallet=wallet, actor=staff).exists()
    payout = PayoutRequest.objects.get(wallet=wallet)
    assert (payout.destination, payout.payment_reference, payout.status) == (
        ERASED,
        "",
        PayoutRequest.Status.PAID,
    )
    assert TopUpRequest.objects.get(user=user).payment_reference == ""
    assert TopUpRequest.objects.get(user=user).resolved_by_id == staff.pk
    assert RewardRefusal.objects.get(key="e:refused").detail == ""
    assert services.audit_wallet(wallet).consistent
    assert report.changed > 0


def test_erasure_is_idempotent(user: Any, staff: Any) -> None:
    history_with_personal_data(user, staff)

    first = anonymise_user(user)
    second = anonymise_user(user)

    assert first.changed > 0
    assert second.changed == 0
    assert asdict(second) == asdict(ErasureReport())


def test_an_open_payout_or_hold_blocks_erasure_until_it_is_resolved(
    user: Any, staff: Any
) -> None:
    services.credit(user, 900, reason=LedgerReason.EARNING, key="b:sale")
    payout = services.request_payout(user, 600, method="bank", destination="DE00 1234")
    hold = services.lock_escrow(user, 50, key="b:hold")

    assert len(erasure_blockers(user)) == 2
    with pytest.raises(ErasureBlocked) as caught:
        anonymise_user(user)
    assert "payout" in str(caught.value) and "escrow" in str(caught.value)
    assert PayoutRequest.objects.get(pk=payout.pk).destination == "DE00 1234"

    services.reject_payout(payout.pk, staff_user=staff, reason_text="Closing account")
    services.cancel_escrow(hold)

    assert erasure_blockers(user) == ()
    assert anonymise_user(user).unreconciled_wallets == 0
    assert services.balance_snapshot(user).total == 900


def test_a_user_without_a_wallet_has_nothing_to_erase_and_gets_none(
    user: Any,
) -> None:
    report = anonymise_user(user)

    assert asdict(report) == asdict(ErasureReport())
    assert not Wallet.objects.filter(user=user).exists()
    assert erasure_blockers(user) == ()


def test_erasing_a_staff_account_unlinks_it_from_other_peoples_records(
    user: Any, staff: Any
) -> None:
    history_with_personal_data(user, staff)

    report = anonymise_user(staff)

    assert report.ledger_actors >= 2
    assert not LedgerEntry.objects.filter(actor=staff).exists()
    assert not TopUpRequest.objects.filter(resolved_by=staff).exists()
    assert not PayoutRequest.objects.filter(reviewed_by=staff).exists()
    assert LedgerEntry.objects.filter(wallet__user=user).exclude(note="").exists()
    assert services.audit_wallet(Wallet.objects.get(user=user)).consistent


def test_the_erasure_contract_is_published_on_the_wallet_api() -> None:
    assert {
        "anonymise_user",
        "erasure_blockers",
        "ErasureBlocked",
        "ErasureReport",
    } <= (set(api.__all__))
    assert api.anonymise_user is anonymise_user
    assert issubclass(api.ErasureBlocked, api.WalletError)
