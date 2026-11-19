"""Contract of ``apps.wallet.api`` with its callers: typed, stable, idempotent.

The ads rewarded-view flow, the shop's credit purchases and the shop's top-up orders
all reach the wallet through this module only. Duplicate delivery of any of them must
never pay or charge twice.
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Any, get_type_hints

import pytest
from django.utils import timezone

from apps.ads.rewarded import EVENT_KEY as ADS_REWARD_EVENT
from apps.core.events.bus import event_bus
from apps.shop import wallet_gateway
from apps.shop.spend_sinks import FILE_PURCHASE, PACKAGE_PURCHASE
from apps.wallet import api
from apps.wallet.models import LedgerEntry, LedgerReason, TopUpRequest
from apps.wallet.services import SHOP_ORDER_PAID_EVENT, sync_registry

pytestmark = pytest.mark.django_db

PUBLIC_CALLABLES = [
    name for name in api.__all__ if inspect.isfunction(getattr(api, name))
]


@pytest.fixture(name="member")
def verified_member(django_user_model: Any) -> Any:
    return django_user_model.objects.create_user(
        username="contract_wallet",
        email="contract_wallet@example.com",
        password="Pass-12345!",
        email_verified_at=timezone.now(),
    )


@pytest.mark.parametrize("name", PUBLIC_CALLABLES)
def test_every_public_function_is_fully_annotated(name: str) -> None:
    function = getattr(api, name)
    signature = inspect.signature(function)

    assert signature.return_annotation is not inspect.Signature.empty, name
    for parameter in signature.parameters.values():
        assert parameter.annotation is not inspect.Parameter.empty, (
            name,
            parameter.name,
        )
    assert get_type_hints(function), name


def test_the_public_names_exist_and_the_gateway_contract_is_covered() -> None:
    assert all(hasattr(api, name) for name in api.__all__)
    assert set(wallet_gateway.CONTRACT) <= set(api.__all__)
    assert wallet_gateway.available()


def whole_credits(value: object) -> bool:
    """An ``int`` that is not a ``bool`` (credits are never floats or flags)."""
    return isinstance(value, int) and not isinstance(value, bool)


def test_credits_are_whole_ints_on_the_public_surface(member: Any) -> None:
    api.credit(member, 100, reason=LedgerReason.TOPUP, key="contract:fund")

    assert whole_credits(api.balance(member))
    assert whole_credits(api.to_credits(Decimal("4.00")))
    snapshot = api.balance_snapshot(member)
    assert all(
        whole_credits(value)
        for value in (
            snapshot.total,
            snapshot.held,
            snapshot.available,
            snapshot.withdrawable,
        )
    )
    fractional: Any = 1.5
    with pytest.raises(api.InvalidAmount):
        api.credit(member, fractional, reason=LedgerReason.TOPUP, key="contract:float")


def test_a_rewarded_ad_delivered_twice_pays_once(member: Any) -> None:
    sync_registry()
    key, reference = "ads:rewarded:501", "rewarded_view:501"

    first = api.grant_reward(member, ADS_REWARD_EVENT, key=key, reference=reference)
    again = api.grant_reward(member, ADS_REWARD_EVENT, key=key, reference=reference)

    assert first is not None and again is not None and again.pk == first.pk
    assert api.balance(member) == first.amount == 5
    assert LedgerEntry.objects.filter(key=key).count() == 1


def test_a_second_view_is_a_second_object_and_the_daily_cap_still_applies(
    member: Any,
) -> None:
    sync_registry()
    paid = [
        api.grant_reward(
            member,
            ADS_REWARD_EVENT,
            key=f"ads:rewarded:{n}",
            reference=f"rewarded_view:{n}",
        )
        for n in range(1, 13)
    ]

    assert [entry is not None for entry in paid] == [True] * 10 + [False] * 2
    assert api.balance(member) == 50


def test_a_shop_credit_purchase_delivered_twice_charges_once(member: Any) -> None:
    sync_registry()
    api.credit(member, 500, reason=LedgerReason.TOPUP, key="contract:fund2")
    key = "shop:file:contract:9"

    for amount in (120, 120, 90):
        wallet_gateway.spend(
            member, FILE_PURCHASE, key=key, reference="file:9", amount=amount
        )

    assert api.balance(member) == 380
    assert LedgerEntry.objects.filter(key=key).count() == 1
    refunded = LedgerEntry.objects.get(key=key)
    api.refund(key, reason="Delivery failed")
    api.refund(key, reason="Delivery failed")
    assert api.balance(member) == 500
    assert refunded.amount == 120


def test_a_shop_purchase_without_enough_credits_books_nothing(member: Any) -> None:
    sync_registry()
    api.credit(member, 30, reason=LedgerReason.TOPUP, key="contract:fund3")

    with pytest.raises(wallet_gateway.InsufficientCreditsError) as caught:
        wallet_gateway.spend(
            member, PACKAGE_PURCHASE, key="shop:pkg:c:1", reference="pkg:1", amount=90
        )

    assert (caught.value.needed, caught.value.available) == (90, 30)
    assert api.balance(member) == 30
    assert not LedgerEntry.objects.filter(key="shop:pkg:c:1").exists()


def test_a_paid_top_up_order_published_twice_credits_once(member: Any) -> None:
    topup = api.request_topup(member, 100)
    TopUpRequest.objects.filter(pk=topup.pk).update(order_reference="contract-77")

    event_bus.publish(SHOP_ORDER_PAID_EVENT, {"order_id": "contract-77"})
    event_bus.publish(SHOP_ORDER_PAID_EVENT, {"order_id": "contract-77"})

    assert api.balance(member) == 100
    assert LedgerEntry.objects.filter(key=f"topup:{topup.pk}").count() == 1
    assert TopUpRequest.objects.get(pk=topup.pk).status == TopUpRequest.Status.PAID


def test_reward_rules_are_exposed_as_read_only_copies(member: Any) -> None:
    sync_registry()

    rules = api.reward_rules([ADS_REWARD_EVENT, "nobody.registered"])

    assert set(rules) == {ADS_REWARD_EVENT}
    rule = rules[ADS_REWARD_EVENT]
    assert (rule.credits, rule.enabled, rule.daily_cap) == (5, True, 50)
    with pytest.raises(FrozenInstanceError):
        delattr(rule, "credits")
    assert api.balance(member) == 0
