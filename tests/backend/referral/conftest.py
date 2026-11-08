"""Fixtures for the referral tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.contrib.sessions.backends.cache import SessionStore
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.referral import services
from apps.wallet.services import sync_registry

PASSWORD = "Pass-12345!"


@pytest.fixture(autouse=True)
def economy_rows(db) -> None:
    sync_registry()


@pytest.fixture
def make_user(django_user_model) -> Callable[..., Any]:
    def build(username: str, *, verified: bool = True) -> Any:
        return django_user_model.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
            email_verified_at=timezone.now() if verified else None,
        )

    return build


@pytest.fixture
def referrer(make_user) -> Any:
    return make_user("referrer")


@pytest.fixture
def staff_client(django_user_model) -> Client:
    staff = django_user_model.objects.create_superuser(
        username="referral_staff", email="referral_staff@example.com"
    )
    client = Client()
    client.force_login(staff)
    return client


@pytest.fixture
def make_request() -> Callable[..., Any]:
    def build(ip: str = "203.0.113.7", path: str = "/") -> Any:
        request = RequestFactory().get(path, REMOTE_ADDR=ip)
        request.session = SessionStore()
        return request

    return build


@pytest.fixture
def invite(referrer, make_user, make_request) -> Callable[..., Any]:
    """Sign a new user up through the referrer's code; returns the invitee."""

    def build(
        username: str = "friend", *, verified: bool = False, ip: str = "198.51.100.9"
    ) -> Any:
        code = services.get_or_create_code(referrer)
        request = make_request(ip=ip)
        assert services.capture_code(request, code.code)
        invitee = make_user(username, verified=verified)
        services.register_signup(invitee, request)
        return invitee

    return build
