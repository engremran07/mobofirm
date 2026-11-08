"""Promotional package credits follow the single dollars × rate conversion."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.promotions import api
from apps.promotions.models import PromotionKind
from apps.wallet.models import WalletSettings

pytestmark = pytest.mark.django_db


def set_rate(rate: int) -> None:
    cfg = WalletSettings.get_solo()
    cfg.credits_per_currency = rate
    cfg.save()


def test_a_dollar_only_discount_derives_its_credits_from_the_rate(
    make_promotion: Any, premium: Any, user: Any
) -> None:
    premium.credit_price_override = None
    premium.save()
    set_rate(10)
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("5.00"),
    )

    quote = api.effective_price(user, premium)

    assert quote is not None
    assert (quote.regular_credit_price, quote.credit_price) == (100, 50)

    set_rate(20)
    quote = api.effective_price(user, premium)
    assert quote is not None
    assert (quote.regular_credit_price, quote.credit_price) == (200, 100)


def test_a_fixed_package_credit_price_scales_with_the_dollar_discount(
    make_promotion: Any, premium: Any, user: Any
) -> None:
    premium.credit_price_override = 90
    premium.save()
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("5.00"),
    )

    quote = api.effective_price(user, premium)

    assert quote is not None
    assert (quote.regular_credit_price, quote.credit_price) == (90, 45)


def test_an_explicit_promotional_credit_price_wins(
    make_promotion: Any, premium: Any, user: Any
) -> None:
    make_promotion(
        PromotionKind.DISCOUNTED_PACKAGE,
        target_package=premium,
        discounted_price=Decimal("5.00"),
        discounted_credit_price=33,
    )

    quote = api.effective_price(user, premium)

    assert quote is not None and quote.credit_price == 33
