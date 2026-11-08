"""Fixtures for the wallet (credits economy) tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone

from apps.wallet import registry
from apps.wallet.models import RewardRule
from apps.wallet.registry import RewardEvent, SpendSink

PASSWORD = "Pass-12345!"

TEST_EVENT = RewardEvent(
    key="test.contribution",
    label="Test contribution",
    app="tests",
    default_credits=10,
)
TEST_DYNAMIC = RewardEvent(
    key="test.bounty",
    label="Test bounty",
    app="tests",
    default_credits=0,
    dynamic_amount=True,
)
TEST_SINK = SpendSink(
    key="test.package", label="Test package", app="tests", default_credits=40
)


@pytest.fixture(autouse=True)
def economy_registered(db) -> None:
    registry.register_reward_event(TEST_EVENT)
    registry.register_reward_event(TEST_DYNAMIC)
    registry.register_spend_sink(TEST_SINK)


@pytest.fixture
def make_user(django_user_model) -> Callable[..., Any]:
    def build(username: str, *, staff: bool = False, verified: bool = True) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            is_staff=staff,
            is_superuser=staff,
            email_verified_at=timezone.now() if verified else None,
        )

    return build


@pytest.fixture
def user(make_user) -> Any:
    return make_user("alice")


@pytest.fixture
def other(make_user) -> Any:
    return make_user("bob")


@pytest.fixture
def staff(make_user) -> Any:
    return make_user("wallet_staff", staff=True)


@pytest.fixture
def rule(db) -> RewardRule:
    from apps.wallet.services import sync_registry

    sync_registry()
    return RewardRule.objects.get(event_key=TEST_EVENT.key)
