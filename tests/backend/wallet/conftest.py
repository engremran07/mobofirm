"""Fixtures for the wallet (credits economy) tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection
from django.test import Client
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
    key="test.package",
    label="Test package",
    app="tests",
    default_credits=40,
    url_name="shop:pricing",
)


def tamper_with_ledger(sql: str, params: list[Any] | None = None) -> None:
    """Change ledger rows below the ORM, as a database console (or a bug) could.

    On PostgreSQL the append-only trigger would refuse this, so it is switched off for
    the rest of the test's transaction (DDL rolls back with it); SQLite has no trigger.
    """
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("ALTER TABLE wallet_ledgerentry DISABLE TRIGGER USER")
        cursor.execute(sql, params)


@pytest.fixture(autouse=True, name="economy_registered")
def register_economy(request: pytest.FixtureRequest) -> None:
    """Every wallet test gets the database and the three test registrations."""
    request.getfixturevalue("db")
    registry.register_reward_event(TEST_EVENT)
    registry.register_reward_event(TEST_DYNAMIC)
    registry.register_spend_sink(TEST_SINK)


@pytest.fixture(name="make_user")
def user_factory(django_user_model: Any) -> Callable[..., Any]:
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


@pytest.fixture(name="user")
def verified_member(make_user: Callable[..., Any]) -> Any:
    return make_user("alice")


@pytest.fixture(name="other")
def second_member(make_user: Callable[..., Any]) -> Any:
    return make_user("bob")


@pytest.fixture(name="staff")
def wallet_staff(make_user: Callable[..., Any]) -> Any:
    return make_user("wallet_staff", staff=True)


@pytest.fixture(name="admin_client")
def signed_in_staff(staff: Any) -> Client:
    """The console client: shadows pytest-django's fixture with the wallet staff user."""
    client = Client()
    client.force_login(staff)
    return client


@pytest.fixture(name="rule")
def event_rule() -> RewardRule:
    """The editable rule row of ``TEST_EVENT``, created from its registered defaults."""
    from apps.wallet.services import sync_registry

    sync_registry()
    return RewardRule.objects.get(event_key=TEST_EVENT.key)
