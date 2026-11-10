"""Operating-system device identity (platform WebAuthn credential).

One OS = one Device row whichever browser is used; the cookie key is only the
fallback. Every rejection path of the server-side verification is covered.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.devices import os_identity
from apps.devices.admin import DeviceAdmin
from apps.devices.models import Device, DeviceBrowserLink, DeviceConfig, DeviceEvent
from apps.devices.services import (
    DEVICE_COOKIE_NAME,
    DevicePolicyError,
    active_devices_queryset,
    approve_current_device,
    enforce_device_policy_for_login,
    find_current_device,
    hash_browser_key,
)
from apps.devices.webauthn import ALG_RS256, b64url_encode
from tests.backend.devices.webauthn_support import (
    WINDOWS_UA,
    PlatformAuthenticator,
    get_options,
    post,
    tamper,
    titles,
)

pytestmark = pytest.mark.django_db


def new_cookie() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:12]  # 44 url-safe characters


def browser(user: Any, cookie: str | None = None, **client_kwargs: Any) -> Client:
    """A browser: its own cookie jar holding its own random device cookie.

    ``force_login`` fires ``user_logged_in`` with a cookie-less request, which would
    register a stray placeholder; only rows the test creates itself are kept.
    """
    known = set(Device.objects.filter(user=user).values_list("pk", flat=True))
    client = Client(HTTP_USER_AGENT=WINDOWS_UA, **client_kwargs)
    client.force_login(user)
    Device.objects.filter(user=user).exclude(pk__in=known).delete()
    client.cookies[DEVICE_COOKIE_NAME] = cookie or new_cookie()
    return client


def register(client: Client, authenticator: PlatformAuthenticator, **kwargs: Any):
    return post(
        client,
        "os_register",
        authenticator.create(get_options(client, "register"), **kwargs),
    )


def verify(client: Client, authenticator: PlatformAuthenticator, **kwargs: Any):
    return post(
        client, "os_verify", authenticator.get(get_options(client, "verify"), **kwargs)
    )


def cookie_of(client: Client) -> str:
    return client.cookies[DEVICE_COOKIE_NAME].value


def sign_in_request(user: Any, cookie: str):
    """A login request as the policy service sees it for a browser with ``cookie``."""
    request = RequestFactory().get("/", HTTP_USER_AGENT=WINDOWS_UA)
    request.COOKIES[DEVICE_COOKIE_NAME] = cookie
    request.user = user
    request.session = SessionStore()
    request.META["REMOTE_ADDR"] = "203.0.113.10"
    return request


# --- registration --------------------------------------------------------------


def test_options_describe_a_platform_credential_without_attestation(user):
    options = get_options(browser(user), "register")["publicKey"]

    assert options["authenticatorSelection"]["authenticatorAttachment"] == "platform"
    assert options["attestation"] == "none"
    assert options["rp"]["id"] == "testserver"
    assert [item["alg"] for item in options["pubKeyCredParams"]] == [-7, -257]
    assert options["excludeCredentials"] == []
    assert str(user.pk) not in options["user"]["id"]


def test_registering_the_os_creates_one_approved_computer(user, authenticator):
    client = browser(user)

    response = register(client, authenticator)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] and body["approved"] and body["created"]
    device = Device.objects.get(user=user)
    assert device.is_os_identity and device.is_trusted
    assert device.browser_key_hash == ""
    assert device.os_credential_rp_id == "testserver"
    assert device.os_credential_alg == -7
    assert device.display_name == "Windows computer"
    assert DeviceBrowserLink.objects.filter(
        user=user,
        device=device,
        browser_key_hash=hash_browser_key(cookie_of(client)),
    ).exists()
    assert "Device registered" in titles(user)
    assert find_current_device(sign_in_request(user, cookie_of(client)), user) == device


def test_rs256_credentials_register_and_verify(user):
    authenticator = PlatformAuthenticator(algorithm=ALG_RS256)
    client = browser(user)

    assert register(client, authenticator).status_code == 200
    assert verify(browser(user), authenticator).status_code == 200


def test_registering_the_same_credential_twice_is_idempotent(user, authenticator):
    client = browser(user)
    register(client, authenticator)

    second = register(client, authenticator)

    assert second.status_code == 200
    assert second.json()["created"] is False
    assert Device.objects.filter(user=user).count() == 1
    assert titles(user).count("Device registered") == 1


def test_same_credential_id_with_another_key_is_a_conflict(user, authenticator):
    register(browser(user), authenticator)
    impostor = PlatformAuthenticator()
    impostor.credential_id = authenticator.credential_id

    response = register(browser(user), impostor)

    assert response.status_code == 409
    assert response.json()["error"] == "credential_conflict"
    assert Device.objects.filter(user=user).count() == 1


def test_registered_credential_is_excluded_from_new_registrations(user, authenticator):
    client = browser(user)
    register(client, authenticator)

    options = get_options(client, "register")["publicKey"]

    assert [item["id"] for item in options["excludeCredentials"]] == [
        authenticator.credential_id_b64
    ]


def test_registration_upgrades_the_browsers_own_fallback_row_in_place(user):
    cookie = new_cookie()
    fallback = Device.objects.create(
        user=user, browser_key_hash=hash_browser_key(cookie), is_trusted=True
    )
    client = browser(user, cookie)

    register(client, PlatformAuthenticator())

    fallback.refresh_from_db()
    assert fallback.is_os_identity and fallback.is_trusted
    assert fallback.browser_key_hash == ""
    assert Device.objects.filter(user=user).count() == 1
    assert active_devices_queryset(user).count() == 1


def test_registration_when_the_allowance_is_full_stays_pending(
    user, authenticator, approve_browsers, settings
):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    approve_browsers(user, 1)

    response = register(browser(user), authenticator)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] and not body["approved"] and body["limit_reached"]
    device = Device.objects.get(user=user, os_credential_id_hash__gt="")
    assert not device.is_trusted
    assert active_devices_queryset(user).count() == 1
    assert "Device registered" not in titles(user)


def test_pending_os_is_approved_through_its_browser_after_a_slot_frees(
    user, authenticator, approve_browsers, settings
):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    (existing,) = approve_browsers(user, 1)
    client = browser(user)
    register(client, authenticator)
    request = sign_in_request(user, cookie_of(client))
    with pytest.raises(DevicePolicyError) as error:
        approve_current_device(request, user)
    assert error.value.reason == "limit_reached"

    existing.is_trusted = False
    existing.save(update_fields=["is_trusted"])
    approved = approve_current_device(request, user)

    assert approved.is_os_identity and approved.is_trusted


def test_registration_respects_the_monthly_registration_quota(
    user, authenticator, approve_browsers
):
    config = DeviceConfig.get_solo()
    config.monthly_device_quota = 1
    config.save(update_fields=["monthly_device_quota"])
    approve_browsers(user, 1)

    response = register(browser(user), authenticator)

    assert response.status_code == 400
    assert response.json()["error"] == "monthly_device_quota"
    assert not Device.objects.filter(user=user, os_credential_id_hash__gt="").exists()


def test_registration_is_atomic_when_the_browser_is_blocked(user, authenticator):
    cookie = new_cookie()
    Device.objects.create(
        user=user, browser_key_hash=hash_browser_key(cookie), is_blocked=True
    )

    response = register(browser(user, cookie), authenticator)

    assert response.status_code == 403
    assert response.json()["error"] == "blocked_device"
    assert not Device.objects.filter(user=user, os_credential_id_hash__gt="").exists()
    assert not DeviceBrowserLink.objects.exists()


# --- verification: browser switch ---------------------------------------------------


def test_another_browser_on_the_same_os_is_recognised_without_a_new_registration(
    user, authenticator
):
    first = browser(user)
    register(first, authenticator)
    second_cookie = new_cookie()
    second = browser(user, second_cookie)

    # Before the OS check the unknown browser is only a placeholder: no alert.
    _, ctx = enforce_device_policy_for_login(sign_in_request(user, second_cookie), user)
    assert ctx["context"]["os_check_pending"] is True
    assert "New device awaiting approval" not in titles(user)

    response = verify(second, authenticator)

    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert Device.objects.filter(user=user).count() == 1
    assert active_devices_queryset(user).count() == 1
    allowed, result = enforce_device_policy_for_login(
        sign_in_request(user, second_cookie), user
    )
    assert allowed and result["is_new"] is False
    assert result["device"].is_os_identity
    assert titles(user) == ["Device registered"]


def test_a_second_browser_does_not_use_a_second_slot(user, authenticator, settings):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    register(browser(user), authenticator)

    assert verify(browser(user), authenticator).status_code == 200
    assert verify(browser(user), authenticator).status_code == 200

    assert active_devices_queryset(user).count() == 1


def test_verifying_folds_the_browsers_pending_placeholder(user, authenticator):
    register(browser(user), authenticator)
    cookie = new_cookie()
    enforce_device_policy_for_login(sign_in_request(user, cookie), user)
    assert Device.objects.filter(user=user, browser_key_hash__gt="").count() == 1

    verify(browser(user, cookie), authenticator)

    assert not Device.objects.filter(user=user, browser_key_hash__gt="").exists()


def test_verifying_hands_a_trusted_fallback_slot_to_the_os(
    user, authenticator, settings
):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    register(browser(user), authenticator)
    cookie = new_cookie()
    Device.objects.create(
        user=user, browser_key_hash=hash_browser_key(cookie), is_trusted=True
    )
    # Staff released the OS slot earlier; the browser kept the only slot.
    Device.objects.filter(user=user, os_credential_id_hash__gt="").update(
        is_trusted=False
    )

    response = verify(browser(user, cookie), authenticator)

    assert response.status_code == 200
    assert response.json()["approved"] is True
    assert active_devices_queryset(user).count() == 1
    assert active_devices_queryset(user).get().is_os_identity


def test_recognition_expires_so_a_restored_browser_profile_must_reverify(
    user, authenticator
):
    client = browser(user)
    register(client, authenticator)
    request = sign_in_request(user, cookie_of(client))
    assert find_current_device(request, user) is not None

    DeviceBrowserLink.objects.update(verified_at=timezone.now() - timedelta(days=31))

    assert find_current_device(request, user) is None
    assert verify(client, authenticator).status_code == 200
    assert find_current_device(request, user) is not None


def test_os_reinstall_voids_the_registration(user, authenticator, settings):
    settings.SHOP_FREE_DEVICE_LIMIT = 1
    register(browser(user), authenticator)
    reinstalled = PlatformAuthenticator()  # a new OS install has no old private key
    client = browser(user)

    options = get_options(client, "verify")
    response = post(client, "os_verify", reinstalled.get(options))
    assert response.status_code == 403
    assert response.json()["error"] == "unknown_credential"

    fresh = register(client, reinstalled).json()
    assert fresh["created"] is True and fresh["limit_reached"] is True
    assert Device.objects.filter(user=user, os_credential_id_hash__gt="").count() == 2


# --- rejection ---------------------------------------------------------------------


def registered(user, authenticator) -> Device:
    register(browser(user), authenticator)
    return Device.objects.get(user=user)


def assert_rejected(response, status: int, reason: str) -> None:
    assert response.status_code == status, response.content
    assert response.json()["ok"] is False
    assert response.json()["error"] == reason


def test_bad_signature_is_rejected_and_audited(user, authenticator):
    device = registered(user, authenticator)
    client = browser(user)
    payload = tamper(authenticator.get(get_options(client, "verify")), "signature")

    assert_rejected(post(client, "os_verify", payload), 403, "bad_signature")

    assert not DeviceBrowserLink.objects.filter(
        browser_key_hash=hash_browser_key(cookie_of(client))
    ).exists()
    device.refresh_from_db()
    assert device.os_credential_sign_count == 0
    assert DeviceEvent.objects.filter(
        user=user, success=False, reason="os_verify_bad_signature"
    ).exists()


def test_signature_over_other_client_data_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    payload = authenticator.get(get_options(client, "verify"))

    assert_rejected(
        post(client, "os_verify", tamper(payload, "authenticatorData")),
        403,
        "bad_signature",
    )


def test_replayed_challenge_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "verify")
    payload = authenticator.get(options)
    assert post(client, "os_verify", payload).status_code == 200

    assert_rejected(post(client, "os_verify", payload), 400, "challenge_missing")


def test_failed_attempt_burns_the_challenge(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "verify")
    assert_rejected(
        post(client, "os_verify", tamper(authenticator.get(options), "signature")),
        403,
        "bad_signature",
    )

    assert_rejected(
        post(client, "os_verify", authenticator.get(options)), 400, "challenge_missing"
    )


def test_expired_challenge_is_rejected(user, authenticator, monkeypatch):
    registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "verify")
    issued = timezone.now().timestamp()
    monkeypatch.setattr(os_identity, "_now_ts", lambda: issued + 3600)

    assert_rejected(
        post(client, "os_verify", authenticator.get(options)), 400, "challenge_expired"
    )


def test_challenge_for_another_purpose_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "register")

    assert_rejected(
        post(client, "os_verify", authenticator.get(options)),
        400,
        "challenge_mismatch",
    )


def test_challenge_is_bound_to_the_account(user, other):
    request = sign_in_request(user, new_cookie())
    os_identity.issue_challenge(request, user, "verify")

    with pytest.raises(os_identity.OsIdentityError) as error:
        os_identity.consume_challenge(request, other, "verify")

    assert error.value.reason == "challenge_mismatch"


def test_wrong_challenge_in_client_data_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    get_options(client, "verify")
    forged = authenticator.get(
        {"publicKey": {"challenge": b64url_encode(b"\x01" * 32)}}
    )

    assert_rejected(post(client, "os_verify", forged), 400, "challenge_mismatch")


def test_wrong_origin_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)

    response = post(
        client,
        "os_verify",
        authenticator.get(get_options(client, "verify"), origin="https://evil.example"),
    )

    assert_rejected(response, 403, "origin_not_allowed")


def test_configured_origin_is_accepted(user, authenticator, settings):
    registered(user, authenticator)
    settings.DEVICE_WEBAUTHN_ALLOWED_ORIGINS = ["https://app.example"]
    client = browser(user)

    response = post(
        client,
        "os_verify",
        authenticator.get(get_options(client, "verify"), origin="https://app.example"),
    )

    assert response.status_code == 200


def test_wrong_rp_id_hash_is_rejected(user, authenticator):
    registered(user, authenticator)
    client = browser(user)

    response = post(
        client,
        "os_verify",
        authenticator.get(get_options(client, "verify"), rp_id="evil.example"),
    )

    assert_rejected(response, 403, "rp_id_mismatch")


def test_credential_of_another_host_is_not_offered(user, authenticator):
    device = registered(user, authenticator)
    device.os_credential_rp_id = "other.example"
    device.save(update_fields=["os_credential_rp_id"])

    response = post(browser(user), "os_options", {"purpose": "verify"})

    assert_rejected(response, 404, "no_credentials")


def test_another_accounts_credential_is_unknown(user, other, authenticator):
    registered(user, authenticator)
    registered(other, PlatformAuthenticator())
    client = browser(other)

    response = post(
        client, "os_verify", authenticator.get(get_options(client, "verify"))
    )

    assert_rejected(response, 403, "unknown_credential")
    assert (
        not DeviceBrowserLink.objects.filter(user=other)
        .exclude(device__user=other)
        .exists()
    )


def test_user_presence_is_required(user, authenticator):
    registered(user, authenticator)
    client = browser(user)

    response = post(
        client,
        "os_verify",
        authenticator.get(get_options(client, "verify"), flags=0x00),
    )

    assert_rejected(response, 403, "user_not_present")


def test_registration_response_must_not_be_an_assertion(user, authenticator):
    client = browser(user)

    response = register(client, authenticator, kind="webauthn.get")

    assert_rejected(response, 400, "wrong_type")
    assert not Device.objects.filter(user=user).exists()


def test_registration_with_another_rp_id_hash_is_rejected(user, authenticator):
    response = register(browser(user), authenticator, rp_id="evil.example")

    assert_rejected(response, 403, "rp_id_mismatch")
    assert not Device.objects.filter(user=user).exists()


def test_registration_with_a_mismatched_credential_id_is_rejected(user, authenticator):
    client = browser(user)
    payload = authenticator.create(get_options(client, "register"))
    payload["credentialId"] = PlatformAuthenticator().credential_id_b64

    assert_rejected(post(client, "os_register", payload), 400, "credential_mismatch")


def test_sign_count_must_increase(user, authenticator):
    registered(user, authenticator)
    client = browser(user)
    assert verify(client, authenticator).status_code == 200

    response = post(
        client,
        "os_verify",
        authenticator.get(get_options(client, "verify"), bump=False),
    )

    assert_rejected(response, 403, "sign_count_regressed")


def test_revoked_os_cannot_be_verified(user, authenticator):
    device = registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "verify")
    device.revoked_at = timezone.now()
    device.is_trusted = False
    device.save(update_fields=["revoked_at", "is_trusted"])

    assert_rejected(
        post(client, "os_verify", authenticator.get(options)), 403, "revoked_device"
    )
    assert_rejected(
        post(browser(user), "os_options", {"purpose": "verify"}), 404, "no_credentials"
    )


def test_blocked_os_cannot_be_verified(user, authenticator):
    device = registered(user, authenticator)
    client = browser(user)
    options = get_options(client, "verify")
    device.is_blocked = True
    device.is_trusted = False
    device.save(update_fields=["is_blocked", "is_trusted"])

    assert_rejected(
        post(client, "os_verify", authenticator.get(options)), 403, "blocked_device"
    )
    assert_rejected(
        post(browser(user), "os_options", {"purpose": "verify"}), 404, "no_credentials"
    )


def test_revoking_a_device_unlinks_its_browsers_and_allows_registering_again(
    user, staff, authenticator
):
    client = browser(user)
    register(client, authenticator)
    device = Device.objects.get(user=user)
    request = RequestFactory().post("/")
    request.user = staff
    model_admin = DeviceAdmin(Device, admin.site)

    with patch.object(model_admin, "message_user") as message_user:
        model_admin.revoke_devices(request, Device.objects.filter(pk=device.pk))

    assert "1 device registration(s) revoked" in message_user.call_args.args[1]
    assert not DeviceBrowserLink.objects.exists()
    assert find_current_device(sign_in_request(user, cookie_of(client)), user) is None
    options = get_options(client, "register")["publicKey"]
    assert options["excludeCredentials"] == []
    assert register(client, PlatformAuthenticator()).json()["created"] is True


# --- endpoint gates ----------------------------------------------------------------


def test_endpoints_require_a_signed_in_user():
    response = post(Client(), "os_options", {"purpose": "register"})

    assert response.status_code == 401


def test_endpoints_require_a_verified_email(make_user):
    unverified = make_user("unverified_os", verified=False)

    response = post(browser(unverified), "os_options", {"purpose": "register"})

    assert response.status_code == 403
    assert response.json()["error"] == "email_not_verified"


def test_a_verified_allauth_address_satisfies_the_email_gate(make_user):
    member = make_user("allauth_only", verified=False)
    EmailAddress.objects.create(
        user=member, email=member.email, primary=True, verified=True
    )

    response = post(browser(member), "os_options", {"purpose": "register"})

    assert response.status_code == 200


def test_endpoints_enforce_csrf(user):
    client = browser(user, enforce_csrf_checks=True)

    response = post(client, "os_options", {"purpose": "register"})

    assert response.status_code == 403


def test_endpoints_accept_post_only(user):
    response = browser(user).get(reverse("devices:os_options"))

    assert response.status_code == 405


@pytest.mark.parametrize("body", ["not json", "[]", '"text"'])
def test_malformed_bodies_are_rejected(user, body):
    response = post(browser(user), "os_options", body)

    assert response.status_code == 400


def test_unknown_purpose_is_rejected(user):
    response = post(browser(user), "os_options", {"purpose": "delete"})

    assert response.status_code == 400


def test_registration_can_be_disabled(user, settings):
    settings.DEVICE_OS_IDENTITY_ENABLED = False

    response = post(browser(user), "os_options", {"purpose": "register"})

    assert response.status_code == 404


def test_endpoints_are_rate_limited(user, monkeypatch):
    monkeypatch.setattr("apps.devices.views.OS_ACTIONS_PER_WINDOW", 2)
    client = browser(user)

    statuses = [
        post(client, "os_options", {"purpose": "register"}).status_code
        for _ in range(3)
    ]

    assert statuses == [200, 200, 429]


# --- UI contract -------------------------------------------------------------------


def test_devices_page_offers_computer_registration_until_the_os_is_recognised(
    user, authenticator
):
    client = browser(user)

    before = client.get(reverse("devices:my_devices"))
    register(client, authenticator)
    after = client.get(reverse("devices:my_devices"))

    assert b"data-device-os" in before.content
    assert b"Register this computer" in before.content
    assert b"data-device-os" not in after.content
    assert b"This computer" in after.content
    assert b"Registered computer (all browsers)" in after.content


def test_devices_page_asks_to_verify_when_an_os_is_already_registered(
    user, authenticator
):
    register(browser(user), authenticator)

    page = browser(user).get(reverse("devices:my_devices"))

    assert b'data-mode="verify"' in page.content
    assert b"Verify this computer" in page.content


def test_admin_labels_computer_and_browser_registrations(
    user, authenticator, approve_browsers
):
    register(browser(user), authenticator)
    approve_browsers(user, 1)
    model_admin = DeviceAdmin(Device, admin.site)

    labels = {
        model_admin.identity(device) for device in Device.objects.filter(user=user)
    }

    assert labels == {"This computer (OS)", "This browser only"}
