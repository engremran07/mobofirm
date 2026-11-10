"""End-to-end device registration through the real sign-in view.

Reproduces the reported defect: a sign-in from an unregistered browser said "New
device registered" while the device was only *pending* (and could not be approved
when the allowance was full), and every additional browser repeated that.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.test import Client
from django.urls import reverse

from apps.devices.models import Device
from apps.devices.services import DEVICE_COOKIE_NAME, active_devices_queryset
from apps.users.models import Notification
from tests.backend.devices.webauthn_support import (
    WINDOWS_UA,
    PlatformAuthenticator,
    post,
    titles,
)

pytestmark = pytest.mark.django_db

PASSWORD = "Pass-12345!"


@pytest.fixture(name="member")
def member_fixture(make_user) -> Any:
    member = make_user("flow_member")
    EmailAddress.objects.create(
        user=member, email=member.email, primary=True, verified=True
    )
    return member


def csrf_token(client: Client, url: str) -> str:
    page = client.get(url)
    found = re.search(
        r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()
    )
    assert found, f"no CSRF token on {url}"
    return found.group(1)


def sign_in(member: Any, client: Client | None = None) -> Client:
    """Sign in through the real view in a browser (cookie jar) with CSRF enforced."""
    client = client or Client(enforce_csrf_checks=True, HTTP_USER_AGENT=WINDOWS_UA)
    response = client.post(
        reverse("users:account_login"),
        {
            "login": member.email,
            "password": PASSWORD,
            "csrfmiddlewaretoken": csrf_token(client, reverse("users:account_login")),
        },
    )
    assert response.status_code == 302, response.content.decode()[:400]
    return client


def approve(client: Client):
    return client.post(
        reverse("devices:acknowledge_new_device"),
        {
            "decision": "approve",
            "csrfmiddlewaretoken": csrf_token(client, reverse("devices:my_devices")),
        },
    )


def test_new_browser_is_pending_until_approved_and_notifications_say_so(member):
    client = sign_in(member)

    device = Device.objects.get(user=member)
    assert not device.is_trusted
    assert client.session["device_registration_prompt"]["can_approve"] is True
    assert titles(member) == ["New device awaiting approval"]
    assert "is not registered yet" in Notification.objects.get().message

    response = approve(client)

    assert response.status_code == 302
    device.refresh_from_db()
    assert device.is_trusted
    assert "device_registration_prompt" not in client.session
    assert titles(member) == ["New device awaiting approval", "Device registered"]


def test_the_same_browser_signing_in_again_is_not_a_new_device(member):
    client = sign_in(member)
    approve(client)
    client.get(reverse("users:account_logout"))
    cookie = client.cookies[DEVICE_COOKIE_NAME].value

    again = Client(enforce_csrf_checks=True, HTTP_USER_AGENT=WINDOWS_UA)
    again.cookies[DEVICE_COOKIE_NAME] = cookie
    sign_in(member, again)

    assert Device.objects.filter(user=member).count() == 1
    assert "device_registration_prompt" not in again.session
    assert titles(member) == ["New device awaiting approval", "Device registered"]


def test_full_allowance_cannot_approve_and_never_claims_a_registration(
    member, approve_browsers, settings
):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    approve_browsers(member, 1)

    client = sign_in(member)

    prompt = client.session["device_registration_prompt"]
    assert prompt["can_approve"] is False
    response = approve(client)
    assert response.status_code == 302
    assert response["Location"] == reverse("devices:my_devices")
    assert not Device.objects.get(
        user=member, browser_key_hash__gt="", is_trusted=False
    ).is_trusted
    assert active_devices_queryset(member).count() == 1
    assert "Device registered" not in titles(member)


def test_os_registered_account_signs_in_from_another_browser_without_a_new_device(
    member,
):
    authenticator = PlatformAuthenticator()
    first = sign_in(member)
    csrf = csrf_token(first, reverse("devices:my_devices"))
    options = post(
        first, "os_options", {"purpose": "register"}, HTTP_X_CSRFTOKEN=csrf
    ).json()
    assert (
        post(
            first,
            "os_register",
            authenticator.create(options),
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 200
    )
    assert Device.objects.filter(user=member).count() == 1
    assert "device_registration_prompt" not in first.session
    first.get(reverse("users:account_logout"))
    before = titles(member)
    assert before == ["New device awaiting approval", "Device registered"]

    # A different browser on the same computer: new cookie jar, same OS credential.
    second = sign_in(member)
    assert client_prompt_mode(second) == "verify"
    assert titles(member) == before
    csrf = csrf_token(second, reverse("devices:my_devices"))
    options = post(
        second, "os_options", {"purpose": "verify"}, HTTP_X_CSRFTOKEN=csrf
    ).json()
    verified = post(
        second, "os_verify", authenticator.get(options), HTTP_X_CSRFTOKEN=csrf
    )

    assert verified.status_code == 200
    assert verified.json()["approved"] is True
    assert "device_registration_prompt" not in second.session
    assert Device.objects.filter(user=member).count() == 1
    assert active_devices_queryset(member).count() == 1
    second.get(reverse("users:account_logout"))

    # Next time that browser signs in it is recognised outright.
    cookie = second.cookies[DEVICE_COOKIE_NAME].value
    third = Client(enforce_csrf_checks=True, HTTP_USER_AGENT=WINDOWS_UA)
    third.cookies[DEVICE_COOKIE_NAME] = cookie
    sign_in(member, third)
    assert "device_registration_prompt" not in third.session
    assert Device.objects.filter(user=member).count() == 1
    assert titles(member) == before


def client_prompt_mode(client: Client) -> str | None:
    """The mode the dialog renders for a client that just signed in."""
    page = client.get(reverse("devices:my_devices"))
    found = re.search(r"data-device-os\s+data-mode=\"(\w+)\"", page.content.decode())
    return found.group(1) if found else None
