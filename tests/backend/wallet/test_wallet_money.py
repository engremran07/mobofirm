"""Money handling: every conversion and label rounds half up, from ``Decimal`` only."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.wallet import services
from apps.wallet.models import LedgerReason, PayoutRequest, WalletSettings
from apps.wallet.services import InvalidAmount

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("amount", "label"),
    [
        (Decimal(4), "$4.00"),
        (Decimal("2.005"), "$2.01"),
        (Decimal("0.125"), "$0.13"),
        (Decimal("0.004"), "$0.00"),
        (Decimal("1234.5"), "$1234.50"),
        (Decimal(-4), "-$4.00"),
        (5, "$5.00"),
    ],
)
def test_currency_is_rounded_half_up_to_cents(amount: Any, label: str) -> None:
    assert services.format_currency(amount) == label


def test_other_currencies_use_their_symbol_or_code() -> None:
    cfg = WalletSettings.get_solo()

    cfg.currency_label = "EUR"
    cfg.save()
    assert services.format_currency(Decimal(4)) == "€4.00"
    cfg.currency_label = "CHF"
    cfg.save()
    assert services.format_currency(Decimal(4)) == "CHF 4.00"


def test_float_amounts_keep_the_value_the_caller_wrote() -> None:
    amount: Any = 4.35

    assert services.to_credits(amount) == 44
    assert services.to_credits(Decimal("4.35")) == 44
    assert services.format_currency(amount) == "$4.35"


@pytest.mark.parametrize("amount", [True, None, "abc", "", Decimal("NaN"), "Infinity"])
def test_non_numeric_and_non_finite_amounts_are_refused(amount: Any) -> None:
    with pytest.raises(InvalidAmount):
        services.to_credits(amount)
    with pytest.raises(InvalidAmount):
        services.format_currency(amount)


def test_numeric_strings_and_negative_amounts() -> None:
    text: Any = "12.5"

    assert services.to_credits(text) == 125
    with pytest.raises(InvalidAmount):
        services.to_credits(Decimal("-0.01"))


def test_a_positive_price_is_never_free_and_zero_stays_zero() -> None:
    assert services.to_credits(Decimal("0.001")) == 1
    assert services.to_credits(Decimal(0)) == 0
    assert services.price_label(Decimal("0.05")) == "$0.05 · 1 credit"
    assert services.price_label(Decimal(4)) == "$4.00 · 40 credits"


def test_credits_convert_back_to_cents_half_up() -> None:
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = 3
    cfg.save()

    assert services.to_currency(1) == Decimal("0.33")
    assert services.to_currency(2) == Decimal("0.67")
    assert services.to_currency(3) == Decimal("1.00")
    assert services.to_currency(0) == Decimal("0.00")


def test_a_payout_cash_amount_is_a_two_decimal_value_after_the_fee(user: Any) -> None:
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = 3
    cfg.payout_fee_percent = Decimal("10.00")
    cfg.save()
    services.credit(user, 500, reason=LedgerReason.EARNING, key="money:sale")

    payout = services.request_payout(user, 500, method="bank", destination="DE00")

    stored = PayoutRequest.objects.get(pk=payout.pk)
    assert stored.payout_amount == Decimal("150.00")
    assert stored.fee_percent == Decimal("10.00")
    assert stored.payout_amount.as_tuple().exponent == -2
