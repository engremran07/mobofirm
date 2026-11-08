"""Fixtures for the promotions tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.promotions import lifecycle
from apps.promotions.models import Promotion, PromotionKind
from apps.shop.models import PackageTier, WindowKind
from apps.wallet import registry
from apps.wallet.models import RewardRule
from apps.wallet.registry import RewardEvent
from apps.wallet.services import sync_registry

PASSWORD = "Pass-12345!"

BOOSTED_EVENT = RewardEvent(
    key="test.promo_event",
    label="Promotion test event",
    app="tests",
    default_credits=10,
    per_object_once=False,
)


@pytest.fixture(autouse=True)
def economy_rows(db) -> None:
    registry.register_reward_event(BOOSTED_EVENT)
    sync_registry()


@pytest.fixture
def make_user(django_user_model) -> Callable[..., Any]:
    def build(
        username: str, *, staff: bool = False, verified: bool = True, days_old: int = 90
    ) -> Any:
        user = django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            is_staff=staff,
            is_superuser=staff,
            email_verified_at=timezone.now() if verified else None,
        )
        django_user_model.objects.filter(pk=user.pk).update(
            date_joined=timezone.now() - timedelta(days=days_old)
        )
        user.refresh_from_db()
        return user

    return build


@pytest.fixture
def user(make_user) -> Any:
    return make_user("offer_user")


@pytest.fixture
def other(make_user) -> Any:
    return make_user("offer_other")


@pytest.fixture
def staff(make_user) -> Any:
    return make_user("offer_staff", staff=True)


@pytest.fixture
def client_for() -> Callable[[Any], Client]:
    def build(account: Any) -> Client:
        client = Client()
        client.force_login(account)
        return client

    return build


@pytest.fixture
def premium(db) -> PackageTier:
    return PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("10.00"),
        credit_price_override=100,
        duration_days=30,
        bandwidth_enabled=True,
        limit_files=30,
        window=WindowKind.MONTH,
        limit_devices=True,
        device_limit=5,
        applies_to_featured=True,
    )


@pytest.fixture
def make_promotion(db) -> Callable[..., Promotion]:
    counter = iter(range(1, 1000))

    def build(
        kind: str = PromotionKind.BONUS_QUOTA,
        *,
        live: bool = True,
        **fields: Any,
    ) -> Promotion:
        number = next(counter)
        defaults: dict[str, Any] = {
            "name": f"Offer {number}",
            "slug": f"offer-{number}",
            "kind": kind,
            "headline": f"Headline {number}",
        }
        if kind == PromotionKind.BONUS_QUOTA:
            defaults |= {"bonus_files": 10, "trial_days": 30}
        if kind == PromotionKind.BONUS_DEVICES:
            defaults |= {"bonus_devices": 2, "trial_days": 30}
        if kind == PromotionKind.CREDITS_BONUS:
            defaults |= {"bonus_credits": 25}
        if kind == PromotionKind.REWARD_MULTIPLIER:
            defaults |= {
                "reward_multiplier": Decimal("2.00"),
                "event_keys": [BOOSTED_EVENT.key],
            }
        defaults.update(fields)
        promotion = Promotion.objects.create(**defaults)
        if live:
            promotion = lifecycle.activate_now(promotion)
        return promotion

    return build


@pytest.fixture
def boosted_rule(db) -> RewardRule:
    return RewardRule.objects.get(event_key=BOOSTED_EVENT.key)
