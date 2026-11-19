"""TOTP codes and recovery codes are single use (audit F-035) and staff MFA is firm (F-015)."""

from __future__ import annotations

import time

import pytest
from allauth.account.models import EmailAddress
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.mfa import TOTPService
from apps.users.models import MFADevice
from apps.users.services.mfa import verify_mfa_code

pytestmark = pytest.mark.django_db

PASSWORD = "Strong-pass-4271!"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


def _member(django_user_model, **flags):
    user = django_user_model.objects.create_user(
        username="totp-member",
        email="totp-member@example.test",
        password=PASSWORD,
        email_verified_at=timezone.now(),
        profile_completed=True,
        **flags,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _enrol(user) -> tuple[MFADevice, str]:
    secret = TOTPService.generate_secret()
    device = MFADevice(user=user, device_type="totp", name="App", is_primary=True)
    device.set_secret(secret)
    device.save()
    return device, secret


def _step(offset: int = 0) -> int:
    """A timestamp ``offset`` 30-second steps from now."""
    return int(time.time()) + 30 * offset


@pytest.fixture(name="member")
def member_fixture(django_user_model):
    return _member(django_user_model)


def test_a_code_is_accepted_once_even_through_a_stale_instance(member):
    device, secret = _enrol(member)
    stale = MFADevice.objects.get(pk=device.pk)
    code = TOTPService.generate_current_code(secret)

    assert device.verify_code(code) is True

    assert stale.last_used_counter is None  # memory still says "never used"
    assert stale.verify_code(code) is False
    assert MFADevice.objects.get(pk=device.pk).last_used_counter is not None


def test_an_older_step_inside_the_window_is_rejected_after_a_newer_one(member):
    device, secret = _enrol(member)
    newer = TOTPService.generate_current_code(secret, at_time=_step(1))
    older = TOTPService.generate_current_code(secret, at_time=_step(-1))

    assert device.verify_code(newer) is True
    assert device.verify_code(older) is False


def test_a_later_step_is_accepted_after_an_earlier_one(member):
    device, secret = _enrol(member)

    assert device.verify_code(TOTPService.generate_current_code(secret)) is True
    assert (
        device.verify_code(TOTPService.generate_current_code(secret, at_time=_step(1)))
        is True
    )


def test_the_storage_step_survives_in_the_database(member):
    device, secret = _enrol(member)
    code = TOTPService.generate_current_code(secret)
    assert verify_mfa_code(member, code) is True

    assert verify_mfa_code(member, code) is False
    assert MFADevice.objects.get(pk=device.pk).last_used_at is not None


def test_a_recovery_code_cannot_be_spent_twice_through_a_stale_instance(member):
    device = MFADevice.objects.create(
        user=member, device_type="backup", name="Recovery codes"
    )
    code = device.generate_backup_codes()[0]
    stale = MFADevice.objects.get(pk=device.pk)

    assert device.verify_backup_code(code.lower()) is True

    assert stale.verify_backup_code(code) is False
    assert len(MFADevice.objects.get(pk=device.pk).backup_codes_hash) == 9


def test_the_sign_in_challenge_rejects_a_replayed_code(member):
    _, secret = _enrol(member)
    code = TOTPService.generate_current_code(secret)
    first, second = Client(), Client()
    for browser in (first, second):
        browser.post(
            reverse("users:account_login"),
            {"login": member.email, "password": PASSWORD},
        )

    accepted = first.post(reverse("users:mfa_verify"), {"code": code})
    replayed = second.post(reverse("users:mfa_verify"), {"code": code})

    assert accepted.status_code == 302
    assert "_auth_user_id" in first.session
    assert replayed.status_code == 302
    assert "_auth_user_id" not in second.session


def test_api_sign_in_rejects_a_replayed_code(member):
    _, secret = _enrol(member)
    code = TOTPService.generate_current_code(secret)
    payload = {"identifier": member.email, "password": PASSWORD, "totp_code": code}

    first = APIClient().post(reverse("users:api_login"), payload, format="json")
    again = APIClient().post(reverse("users:api_login"), payload, format="json")

    assert first.status_code == 200
    assert again.status_code == 401


def test_the_enrolment_code_cannot_be_reused_to_sign_in(member):
    client = Client()
    client.force_login(member)
    client.get(reverse("users:mfa_setup"))
    secret = client.session["pending_mfa_setup_secret"]
    code = TOTPService.generate_current_code(secret)
    assert client.post(reverse("users:mfa_setup"), {"code": code}).status_code == 302

    reused = verify_mfa_code(member, code)

    assert reused is False


# --- F-015: staff MFA is mandatory and cannot be switched off ------------------------


@pytest.fixture(name="staff_member")
def staff_member_fixture(django_user_model, settings):
    settings.STAFF_MFA_REQUIRED = True
    return _member(django_user_model, is_staff=True)


def test_staff_cannot_disable_their_authenticator(staff_member):
    _, secret = _enrol(staff_member)
    client = Client()
    client.force_login(staff_member)
    session = client.session
    session["mfa_verified_user_id"] = str(staff_member.pk)
    session.save()

    response = client.post(
        reverse("users:mfa_disable"),
        {"code": TOTPService.generate_current_code(secret, at_time=_step(1))},
    )

    assert response.status_code == 403
    assert MFADevice.objects.filter(user=staff_member).exists()


def test_staff_api_sign_in_requires_an_enrolled_authenticator(staff_member):
    response = APIClient().post(
        reverse("users:api_login"),
        {"identifier": staff_member.email, "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 403
    assert response.data["mfa_setup_required"] is True


def test_staff_api_sign_in_needs_the_code_and_then_succeeds(staff_member):
    _, secret = _enrol(staff_member)
    credentials = {"identifier": staff_member.email, "password": PASSWORD}

    missing = APIClient().post(reverse("users:api_login"), credentials, format="json")
    allowed = APIClient().post(
        reverse("users:api_login"),
        {**credentials, "totp_code": TOTPService.generate_current_code(secret)},
        format="json",
    )

    assert missing.status_code == 403
    assert missing.data["mfa_required"] is True
    assert allowed.status_code == 200
