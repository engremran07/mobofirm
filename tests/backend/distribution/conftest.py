"""Fixtures for the distribution tests: fake transport (no network), in-memory media
storage, channel and content factories."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client

from apps.distribution import services
from apps.distribution.content import Content
from apps.distribution.models import Channel
from apps.seo.fetcher import FetchResult, SafeFetcher
from apps.site_settings.models import SiteSettings

SITE = "https://mobofirm.example"
PASSWORD = "Pass-12345!"

DEFAULT_VALUES: dict[str, dict[str, str]] = {
    "webhook": {"endpoint_url": "https://hooks.example.com/in"},
    "telegram": {"chat_id": "@mobofirm", "bot_token": "123:BOT-TOKEN"},
    "discord": {"webhook_url": "https://discord.com/api/webhooks/1/abc"},
    "slack": {"webhook_url": "https://hooks.slack.com/services/T0/B0/xyz"},
    "x": {"access_token": "x-user-token"},
    "mastodon": {
        "instance_url": "https://mastodon.example",
        "access_token": "masto-token",
    },
    "bluesky": {
        "handle": "mobofirm.bsky.social",
        "app_password": "abcd-efgh-ijkl-mnop",
    },
    "facebook": {"page_id": "12345", "access_token": "fb-page-token"},
    "instagram": {
        "ig_user_id": "1784",
        "access_token": "ig-token",
        "format": "feed",
    },
    "threads": {"threads_user_id": "99", "access_token": "th-token"},
    "linkedin": {
        "author_urn": "urn:li:organization:123",
        "access_token": "li-token",
    },
    "reddit": {
        "subreddit": "androidflashing",
        "client_id": "rid-client",
        "client_secret": "rsecret-1234",
        "refresh_token": "rrefresh-1234",
    },
    "pinterest": {"board_id": "555", "access_token": "pin-token"},
    "tiktok": {"access_token": "tt-token", "privacy_level": "SELF_ONLY"},
    "youtube": {
        "client_id": "yid-client",
        "client_secret": "ysecret-1234",
        "refresh_token": "yrefresh-1234",
    },
    "whatsapp": {
        "phone_number_id": "777",
        "recipients": "+15551234567,+4915112345678",
        "template_name": "news_update",
        "access_token": "wa-token",
    },
}


class FakeFetcher(SafeFetcher):
    """Records requests and replays scripted outcomes; never touches the network.

    ``routes`` maps a URL fragment to an outcome and wins over the sequential list.
    """

    def __init__(
        self,
        *outcomes: FetchResult | Exception,
        routes: Mapping[str, FetchResult | Exception] | None = None,
    ) -> None:
        super().__init__()
        self.outcomes = list(outcomes)
        self.routes = dict(routes or {})
        self.calls: list[dict[str, Any]] = []

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        follow_redirects: bool = True,
        allowed_hosts: frozenset[str] | None = None,
    ) -> FetchResult:
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": dict(headers or {}),
                "body": body,
                "follow_redirects": follow_redirects,
                "allowed_hosts": allowed_hosts,
            }
        )
        outcome: FetchResult | Exception
        match = next((frag for frag in self.routes if frag in url), None)
        if match is not None:
            outcome = self.routes[match]
        elif self.outcomes:
            outcome = self.outcomes.pop(0)
        else:
            outcome = ok()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def bodies(self) -> list[dict[str, Any]]:
        decoded = []
        for call in self.calls:
            try:
                decoded.append(json.loads(call["body"] or b"{}"))
            except (ValueError, UnicodeDecodeError):
                decoded.append({})
        return decoded


def ok(body: bytes | dict[str, Any] = b"{}", status: int = 200, **headers: str):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return FetchResult(url="u", final_url="u", status=status, headers=headers, body=raw)


def reply(status: int, **headers: str):
    return FetchResult(url="u", final_url="u", status=status, headers=headers, body=b"")


@pytest.fixture
def default_values() -> dict[str, dict[str, str]]:
    """Complete credential values for every platform."""
    return DEFAULT_VALUES


@pytest.fixture
def net() -> SimpleNamespace:
    """``net.Fetcher(...)`` fake transport, ``net.ok(...)`` / ``net.reply(status)``."""
    return SimpleNamespace(Fetcher=FakeFetcher, ok=ok, reply=reply)


@pytest.fixture(autouse=True)
def site_url(db):
    row = SiteSettings.get_solo()
    row.site_url = SITE
    row.save()


@pytest.fixture(autouse=True)
def memory_storage(settings):
    """Share cards and videos never touch the real MEDIA folder in tests."""
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }


@pytest.fixture
def make_channel() -> Callable[..., Channel]:
    def build(
        kind: str = "webhook",
        values: Mapping[str, str] | None = None,
        **extra: Any,
    ) -> Channel:
        extra.setdefault("use_composer", False)
        extra.setdefault("name", f"{kind}-channel")
        extra.setdefault("enabled", True)
        channel = Channel(kind=kind, **extra)
        services.apply_values(channel, {**DEFAULT_VALUES[kind], **(values or {})})
        channel.save()
        return channel

    return build


@pytest.fixture
def make_content() -> Callable[..., Content]:
    def build(
        ref: str = "official:abc",
        title: str = "Acme Phone One Global",
        kind: str = "firmware",
        **extra: Any,
    ) -> Content:
        base: dict[str, Any] = {
            "kind": kind,
            "object_ref": ref,
            "title": title,
            "url": f"{SITE}/firmwares/file/official/{ref.split(':')[-1]}/",
        }
        if kind == "firmware":
            base.update(brand="Acme", model="Phone One", variant="Global")
        return Content(**{**base, **extra})

    return build


@pytest.fixture
def admin_client(db) -> Client:
    admin = get_user_model().objects.create_superuser(
        username="d-admin", email="d-admin@example.com", password=PASSWORD
    )
    client = Client()
    client.force_login(admin)
    return client


@pytest.fixture
def staff_client(db) -> Callable[..., Client]:
    def build(*codenames: str) -> Client:
        user = get_user_model().objects.create_user(
            username=f"staff-{'-'.join(codenames) or 'none'}",
            email=f"{'-'.join(codenames) or 'none'}@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="distribution", codename__in=codenames
            )
        )
        client = Client()
        client.force_login(user)
        return client

    return build
