"""Public and account navigation follows the enabled community-app flags."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.core.models import AppRegistry

pytestmark = pytest.mark.django_db


def _set_community_apps(*, bounty: bool, gamification: bool) -> None:
    registry = AppRegistry.get_solo()
    registry.bounty_enabled = bounty
    registry.gamification_enabled = gamification
    registry.save(update_fields=["bounty_enabled", "gamification_enabled"])


def test_enabled_community_apps_are_linked_from_the_public_shell_and_account(
    client,
):
    _set_community_apps(bounty=True, gamification=True)
    user = get_user_model().objects.create_user(
        username="community-navigation-member",
        email="community-navigation-member@example.test",
        email_verified_at=timezone.now(),
    )
    client.force_login(user)

    public = client.get(reverse("blog:post_list"))
    public_html = public.content.decode()
    assert public.status_code == 200
    for route_name in (
        "bounty:list",
        "gamification:leaderboard",
        "gamification:badges",
    ):
        assert reverse(route_name) in public_html

    account = client.get(reverse("users:dashboard"))
    account_html = account.content.decode()
    assert account.status_code == 200
    for label, route_name in (
        ("My bounties", "bounty:mine"),
        ("My progress", "gamification:progress"),
        ("Community badges", "gamification:badges"),
    ):
        assert label in account_html
        assert reverse(route_name) in account_html


def test_disabled_community_apps_are_hidden_and_gamification_routes_are_closed(
    client,
):
    _set_community_apps(bounty=False, gamification=False)
    user = get_user_model().objects.create_user(
        username="community-navigation-disabled",
        email="community-navigation-disabled@example.test",
        email_verified_at=timezone.now(),
    )
    client.force_login(user)

    public = client.get(reverse("blog:post_list"))
    account = client.get(reverse("users:dashboard"))
    assert public.status_code == 200
    assert account.status_code == 200
    for path in (
        reverse("bounty:list"),
        reverse("gamification:leaderboard"),
        reverse("gamification:badges"),
        reverse("gamification:progress"),
    ):
        assert path not in public.content.decode()
        assert path not in account.content.decode()

    assert client.get(reverse("bounty:list")).status_code == 404
    assert client.get(reverse("gamification:leaderboard")).status_code == 404
    assert client.get(reverse("gamification:badges")).status_code == 404
    assert client.get(reverse("gamification:progress")).status_code == 404


def test_account_workspace_links_optional_user_services_from_app_flags(client):
    registry = AppRegistry.get_solo()
    registry.blog_enabled = True
    registry.wallet_enabled = True
    registry.referral_enabled = True
    registry.save(update_fields=["blog_enabled", "wallet_enabled", "referral_enabled"])
    user = get_user_model().objects.create_user(
        username="account-workspace-member",
        email="account-workspace-member@example.test",
        email_verified_at=timezone.now(),
    )
    client.force_login(user)

    account_html = client.get(reverse("users:dashboard")).content.decode()
    for label, route_name in (
        ("Purchased articles", "blog:purchased"),
        ("Wallet", "wallet:dashboard"),
        ("Referrals", "referral:dashboard"),
    ):
        assert label in account_html
        assert reverse(route_name) in account_html

    registry.blog_enabled = False
    registry.wallet_enabled = False
    registry.referral_enabled = False
    registry.save(update_fields=["blog_enabled", "wallet_enabled", "referral_enabled"])
    account_html = client.get(reverse("users:dashboard")).content.decode()
    for route_name in ("blog:purchased", "wallet:dashboard", "referral:dashboard"):
        assert reverse(route_name) not in account_html
