"""Fixtures for the device slot and device-limit tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.devices.models import Device
from apps.devices.services import hash_browser_key
from tests.backend.devices.webauthn_support import PlatformAuthenticator

PASSWORD = "Pass-12345!"


@pytest.fixture(autouse=True)
def clear_rate_limit_cache() -> None:
    """The OS-identity endpoints are rate limited per account in the shared cache."""
    cache.clear()


@pytest.fixture(name="authenticator")
def authenticator_fixture() -> PlatformAuthenticator:
    return PlatformAuthenticator()


@pytest.fixture(name="make_user")
def make_user_fixture(django_user_model) -> Callable[..., Any]:
    def build(username: str, *, superuser: bool = False, verified: bool = True) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            is_staff=superuser,
            is_superuser=superuser,
            email_verified_at=timezone.now() if verified else None,
        )

    return build


@pytest.fixture(name="user")
def user_fixture(make_user) -> Any:
    return make_user("slot_user")


@pytest.fixture(name="other")
def other_fixture(make_user) -> Any:
    return make_user("slot_other")


@pytest.fixture(name="staff")
def staff_fixture(make_user) -> Any:
    return make_user("slot_staff", superuser=True)


@pytest.fixture(name="approve_browsers")
def approve_browsers_fixture() -> Callable[..., list[Device]]:
    """Register ``count`` approved browsers for ``user``."""

    def build(user: Any, count: int, *, prefix: str = "k") -> list[Device]:
        return [
            Device.objects.create(
                user=user,
                browser_key_hash=hash_browser_key(f"{prefix}{index}".ljust(43, "x")),
                is_trusted=True,
                display_name=f"Browser {index}",
            )
            for index in range(count)
        ]

    return build
