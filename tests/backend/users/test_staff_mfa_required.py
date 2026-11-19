"""Staff and superusers must hold a verified authenticator to reach the admin plane."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.users.mfa import TOTPService
from apps.users.models import MFADevice

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _staff_mfa_enforced(settings):
    settings.STAFF_MFA_REQUIRED = True


def _enrol(user) -> None:
    device = MFADevice.objects.create(
        user=user, device_type="totp", name="Authenticator", is_active=True
    )
    device.set_secret(TOTPService.generate_secret())
    device.save(update_fields=["secret_encrypted"])


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture(name="staff_member")
def staff_member_fixture(django_user_model):
    return django_user_model.objects.create_user(
        username="mfa-staff",
        email="mfa-staff@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )


@pytest.mark.parametrize("flags", [{"is_staff": True}, {"is_superuser": True}])
def test_privileged_users_without_a_device_are_sent_to_setup(django_user_model, flags):
    user = django_user_model.objects.create_user(
        username="mfa-privileged",
        email="mfa-privileged@example.test",
        email_verified_at=timezone.now(),
        **flags,
    )

    response = _client(user).get(reverse("admin:index"))

    assert response.status_code == 302
    assert response["Location"] == reverse("users:mfa_setup")


def test_an_enrolled_staff_user_must_verify_the_session_first(staff_member):
    _enrol(staff_member)
    client = _client(staff_member)

    pending = client.get(reverse("admin:index"))
    session = client.session
    session["mfa_verified_user_id"] = str(staff_member.pk)
    session.save()
    verified = client.get(reverse("admin:index"))

    assert pending.status_code == 302
    assert pending["Location"] == reverse("users:mfa_verify")
    assert verified.status_code == 200


def test_the_requirement_does_not_apply_to_ordinary_members(django_user_model):
    member = django_user_model.objects.create_user(
        username="mfa-member",
        email="mfa-member@example.test",
        email_verified_at=timezone.now(),
        profile_completed=True,
    )

    response = _client(member).get(reverse("users:dashboard"))

    assert response.status_code != 302 or response["Location"] not in {
        reverse("users:mfa_setup"),
        reverse("users:mfa_verify"),
    }
