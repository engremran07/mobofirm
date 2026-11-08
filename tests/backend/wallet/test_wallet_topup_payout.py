"""Offline top-ups and staff-reviewed payouts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied

from apps.wallet import services
from apps.wallet.models import (
    LedgerEntry,
    LedgerReason,
    PayoutRequest,
    TopUpRequest,
    Wallet,
    WalletSettings,
)
from apps.wallet.services import InsufficientCredits, PayoutError, TopUpError

pytestmark = pytest.mark.django_db


def earn(user: Any, credits: int, key: str = "sale:1") -> None:
    services.credit(user, credits, reason=LedgerReason.EARNING, key=key)


def test_a_topup_request_prices_credits_with_the_configured_rate(user: Any) -> None:
    request = services.request_topup(user, 100)

    assert (request.credits, request.price, request.currency_label) == (
        100,
        Decimal("10.00"),
        "USD",
    )
    assert request.status == TopUpRequest.Status.PENDING
    assert services.balance(user) == 0


def test_topup_limits_and_switch_are_enforced(user: Any) -> None:
    with pytest.raises(TopUpError):
        services.request_topup(user, 10)
    with pytest.raises(TopUpError):
        services.request_topup(user, 10**7)

    cfg = WalletSettings.get_solo()
    cfg.max_open_topups = 1
    cfg.save()
    services.request_topup(user, 100)
    with pytest.raises(TopUpError):
        services.request_topup(user, 100)

    cfg.topups_enabled = False
    cfg.save()
    with pytest.raises(TopUpError):
        services.request_topup(user, 100)


def test_staff_confirmation_grants_credits_exactly_once(user: Any, staff: Any) -> None:
    request = services.request_topup(user, 100)

    services.confirm_topup(request.pk, staff_user=staff, reference="receipt 9")
    services.confirm_topup(request.pk, staff_user=staff, reference="receipt 9")

    request.refresh_from_db()
    assert request.status == TopUpRequest.Status.PAID
    assert request.resolved_by_id == staff.pk
    assert services.balance(user) == 100
    assert LedgerEntry.objects.filter(key=f"topup:{request.pk}").count() == 1


def test_only_staff_with_the_permission_can_confirm(user: Any, other: Any) -> None:
    request = services.request_topup(user, 100)

    with pytest.raises(PermissionDenied):
        services.confirm_topup(request.pk, staff_user=other)
    assert services.balance(user) == 0


def test_a_cancelled_topup_cannot_be_confirmed(user: Any, staff: Any) -> None:
    request = services.request_topup(user, 100)

    services.cancel_topup(request.pk, user=user)

    with pytest.raises(TopUpError):
        services.confirm_topup(request.pk, staff_user=staff)


def test_users_cannot_cancel_someone_elses_topup(user: Any, other: Any) -> None:
    request = services.request_topup(user, 100)

    with pytest.raises(PermissionDenied):
        services.cancel_topup(request.pk, user=other)


def test_a_paid_shop_order_event_confirms_the_linked_topup(user: Any) -> None:
    request = services.request_topup(user, 100)
    TopUpRequest.objects.filter(pk=request.pk).update(order_reference="42")

    services.handle_shop_order_paid({"order_id": 42})
    services.handle_shop_order_paid({"order_id": 42})
    services.handle_shop_order_paid({"order_id": 999})
    services.handle_shop_order_paid("garbage")

    assert services.balance(user) == 100


def test_request_topup_asks_the_shop_for_an_order_when_it_offers_one(
    user: Any,
) -> None:
    class ShopApi:
        @staticmethod
        def create_credit_topup_order(
            user: Any, *, credit_amount: int, price: Decimal, reference: str
        ) -> int:
            assert (credit_amount, price) == (100, Decimal("10.00"))
            assert reference.startswith("wallet:topup:")
            return 77

    with patch("apps.core.app_service.AppService.get", return_value=ShopApi):
        request = services.request_topup(user, 100)

    assert request.order_reference == "77"


def test_only_earned_credits_can_be_paid_out(user: Any) -> None:
    services.credit(user, 1000, reason=LedgerReason.TOPUP, key="bought")
    earn(user, 600)

    assert services.balance(user) == 1600
    assert services.withdrawable_balance(user) == 600
    with pytest.raises(InsufficientCredits):
        services.request_payout(user, 700, method="bank", destination="DE00 1234")


def test_a_payout_holds_the_credits_and_computes_the_cash_value(user: Any) -> None:
    cfg = WalletSettings.get_solo()
    cfg.payout_fee_percent = Decimal("10.00")
    cfg.save()
    earn(user, 600)

    payout = services.request_payout(user, 500, method="paypal", destination="a@b.c")

    assert (payout.credits, payout.payout_amount, payout.fee_percent) == (
        500,
        Decimal("45.00"),
        Decimal("10.00"),
    )
    assert services.balance(user) == 100
    assert services.withdrawable_balance(user) == 100
    assert Wallet.objects.get(user=user).balance == 100


def test_payout_validation(user: Any) -> None:
    earn(user, 900)

    with pytest.raises(PayoutError):
        services.request_payout(user, 100, method="bank", destination="x")
    with pytest.raises(PayoutError):
        services.request_payout(user, 600, method="cash", destination="x")
    with pytest.raises(PayoutError):
        services.request_payout(user, 600, method="bank", destination="  ")
    services.request_payout(user, 600, method="bank", destination="DE00")
    with pytest.raises(PayoutError):
        services.request_payout(user, 600, method="bank", destination="DE00")

    cfg = WalletSettings.get_solo()
    cfg.payouts_enabled = False
    cfg.save()
    with pytest.raises(PayoutError):
        services.request_payout(user, 600, method="bank", destination="DE00")


def test_approve_then_mark_paid_keeps_the_credits_deducted(
    user: Any, staff: Any
) -> None:
    earn(user, 600)
    payout = services.request_payout(user, 600, method="bank", destination="DE00")

    with pytest.raises(PayoutError):
        services.mark_payout_paid(payout.pk, staff_user=staff)
    services.approve_payout(payout.pk, staff_user=staff)
    services.mark_payout_paid(payout.pk, staff_user=staff, reference="wire 1")
    services.mark_payout_paid(payout.pk, staff_user=staff, reference="wire 1")

    payout.refresh_from_db()
    assert payout.status == PayoutRequest.Status.PAID
    assert payout.payment_reference == "wire 1"
    assert services.balance(user) == 0
    assert services.withdrawable_balance(user) == 0
    with pytest.raises(PayoutError):
        services.reject_payout(payout.pk, staff_user=staff, reason_text="too late")


def test_rejecting_a_payout_returns_the_credits_once(user: Any, staff: Any) -> None:
    earn(user, 600)
    payout = services.request_payout(user, 600, method="bank", destination="DE00")

    services.reject_payout(payout.pk, staff_user=staff, reason_text="Bad account")
    services.reject_payout(payout.pk, staff_user=staff, reason_text="Bad account")

    payout.refresh_from_db()
    assert payout.status == PayoutRequest.Status.REJECTED
    assert payout.decision_reason == "Bad account"
    assert services.balance(user) == 600
    assert services.withdrawable_balance(user) == 600
    assert LedgerEntry.objects.filter(key=f"payout:{payout.pk}:reversal").count() == 1
    assert services.audit_wallet(Wallet.objects.get(user=user)).consistent
    with pytest.raises(PayoutError):
        services.reject_payout(
            services.request_payout(user, 600, method="bank", destination="DE01").pk,
            staff_user=staff,
            reason_text=" ",
        )


def test_payout_decisions_need_the_permission(user: Any, other: Any) -> None:
    earn(user, 600)
    payout = services.request_payout(user, 600, method="bank", destination="DE00")

    for decide in (
        lambda: services.approve_payout(payout.pk, staff_user=other),
        lambda: services.mark_payout_paid(payout.pk, staff_user=other),
        lambda: services.reject_payout(payout.pk, staff_user=other, reason_text="no"),
    ):
        with pytest.raises(PermissionDenied):
            decide()
    payout.refresh_from_db()
    assert payout.status == PayoutRequest.Status.PENDING
