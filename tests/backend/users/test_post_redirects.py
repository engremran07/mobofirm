"""Successful browser POSTs in the account area redirect (POST-Redirect-GET).

A page that answers a successful POST directly makes the browser show "Confirm Form
Resubmission" on refresh. The account pages below must answer 302 and show the result
on the following GET.
"""

from __future__ import annotations

import re

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db

OLD = "Old-pass-84213!"
NEW = "Brand-New-Pass-5571!"


def _member(*, usable_password: bool = True, completed: bool = True):
    user = get_user_model().objects.create_user(
        username="prguser",
        email="prguser@example.test",
        password=OLD if usable_password else None,
        email_verified_at=timezone.now(),
        profile_completed=completed,
        full_name="Prg User",
        country="PK",
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def test_profile_update_redirects_and_the_next_get_shows_the_result(client):
    client.force_login(_member())

    response = client.post(
        reverse("users:profile"),
        {"action": "update_profile", "full_name": "Renamed Person", "country": "PK"},
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("users:profile")
    page = client.get(response["Location"])
    assert page.status_code == 200
    assert "Profile updated successfully." in page.content.decode()


@pytest.mark.parametrize("current", [OLD, "wrong-current-password"])
def test_change_password_redirects_whether_it_worked_or_not(client, current):
    user = _member()
    client.force_login(user)

    response = client.post(
        reverse("users:profile"),
        {
            "action": "change_password",
            "current_password": current,
            "new_password1": NEW,
            "new_password2": NEW,
        },
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password(NEW) is (current == OLD)


def test_notification_settings_redirect_after_saving(client):
    client.force_login(_member())

    response = client.post(
        reverse("users:notification_settings"), {"email_comments": "on"}
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("users:notification_settings")


def test_a_social_account_sets_its_password_during_profile_completion(client):
    user = _member(usable_password=False, completed=False)
    client.force_login(user)

    response = client.post(
        reverse("users:tell_us_about_you"),
        {
            "username": "prguser",
            "full_name": "Prg User",
            "country": "PK",
            "interests": ["marketplace"],
            "password1": NEW,
            "password2": NEW,
        },
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password(NEW)
    assert user.profile_completed is True
    # The session survives the password change, so the redirect target loads.
    assert client.get(response["Location"]).status_code == 200


def test_a_rejected_password_re_renders_with_the_inline_error_container(client):
    user = _member(usable_password=False, completed=False)
    client.force_login(user)

    response = client.post(
        reverse("users:tell_us_about_you"),
        {
            "username": "prguser",
            "full_name": "Prg User",
            "country": "PK",
            "interests": ["marketplace"],
            "password1": "12345678",
            "password2": "12345678",
        },
    )

    assert response.status_code == 200
    assert (
        "This password is entirely numeric."
        in response.context["form"].errors["password1"]
    )
    html = response.content.decode()
    container = re.search(
        r'<div[^>]*id="id_password1_errors"[^>]*>(.*?)</div>', html, re.DOTALL
    )
    assert container is not None
    assert "entirely numeric" in container.group(1)
    assert 'aria-invalid="true"' in html
