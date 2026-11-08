"""Referral pages and admin: authorization, isolation, loading."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib import admin
from django.test import Client
from django.urls import reverse

from apps.referral import services
from apps.referral.models import Referral

pytestmark = pytest.mark.django_db


def signed_in(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_dashboard_requires_sign_in(client: Client) -> None:
    response = client.get(reverse("referral:dashboard"))

    assert response.status_code == 302
    assert "/login" in response["Location"]


def test_dashboard_shows_the_personal_link_rules_and_invitees(
    referrer: Any, invite: Any
) -> None:
    invite("friendly", verified=False)
    code = services.get_or_create_code(referrer).code

    html = signed_in(referrer).get(reverse("referral:dashboard")).content.decode()

    assert f"/referral/r/{code}/" in html
    assert "friendly" in html
    assert "20 credits" in html
    assert "One reward per friend" in html


def test_dashboard_never_lists_another_referrers_invitees(
    referrer: Any, invite: Any, make_user: Any
) -> None:
    invite("secret_friend")
    stranger = make_user("stranger")

    html = signed_in(stranger).get(reverse("referral:dashboard")).content.decode()

    assert "secret_friend" not in html
    assert services.get_or_create_code(referrer).code not in html


def test_dashboard_is_404_when_the_flag_is_off(referrer: Any) -> None:
    from apps.core.models import AppRegistry

    registry = AppRegistry.get_solo()
    registry.referral_enabled = False
    registry.save()

    assert signed_in(referrer).get(reverse("referral:dashboard")).status_code == 404


def test_the_landing_url_only_accepts_get(client: Client, referrer: Any) -> None:
    code = services.get_or_create_code(referrer).code

    assert client.post(reverse("referral:landing", args=[code])).status_code == 405


def test_every_referral_model_is_registered_and_loads_in_the_admin(
    staff_client: Client, invite: Any
) -> None:
    invite()
    registered = {
        m._meta.model_name
        for m in admin.site._registry
        if m._meta.app_label == "referral"
    }
    assert registered == {"referralsettings", "referralcode", "referral"}

    for name in registered:
        url = reverse(f"admin:referral_{name}_changelist")
        assert staff_client.get(url, follow=True).status_code == 200, name


def test_the_referral_admin_is_read_only_for_invitations(
    staff_client: Client, invite: Any
) -> None:
    invite()
    referral = Referral.objects.get()

    assert (
        staff_client.get(
            reverse("admin:referral_referral_change", args=[referral.pk])
        ).status_code
        == 200
    )
    assert staff_client.get(reverse("admin:referral_referral_add")).status_code == 403
    assert (
        staff_client.post(
            reverse("admin:referral_referral_delete", args=[referral.pk]),
            {"post": "yes"},
        ).status_code
        == 403
    )


def test_the_reevaluate_action_pays_pending_rewards(
    staff_client: Client, invite: Any, referrer: Any
) -> None:
    from django.utils import timezone

    invitee = invite(verified=True)
    Referral.objects.filter(invitee=invitee).update(qualified_at=timezone.now())

    staff_client.post(
        reverse("admin:referral_referral_changelist"),
        {"action": "reevaluate", "_selected_action": [Referral.objects.get().pk]},
    )

    assert Referral.objects.get().status == "rewarded"
