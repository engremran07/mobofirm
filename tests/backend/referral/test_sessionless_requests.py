"""Signup-signal requests may carry no session; referral capture must tolerate them."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.referral import services

pytestmark = pytest.mark.django_db


@pytest.fixture
def invitee(django_user_model):
    return django_user_model.objects.create_user(
        username="invitee", email="invitee@example.com", password="Pass-12345!"
    )


def test_registering_a_signup_without_a_session_is_a_no_op(invitee):
    request = RequestFactory().post("/signup/")
    assert not hasattr(request, "session")
    assert services.captured_code(request) == ""
    assert services.register_signup(invitee, request) is None
    assert services.register_signup(invitee, None) is None


def test_capturing_a_code_without_a_session_does_not_crash(invitee):
    owner = services.get_or_create_code(invitee)
    request = RequestFactory().get("/", {"ref": owner.code})
    assert services.capture_code(request, owner.code) is False
