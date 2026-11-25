from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.referral import services
from apps.users.forms import CustomSignupForm, TellUsAboutYouForm

pytestmark = pytest.mark.django_db


def test_manual_signup_accepts_an_optional_valid_referral_code(referrer) -> None:
    code = services.get_or_create_code(referrer)
    form = CustomSignupForm(
        data={
            "email": "invitee@example.test",
            "username": "invitee",
            "password1": "Complex-Password-123!",
            "password2": "Complex-Password-123!",
            "referral_code": code.code,
        },
        request=RequestFactory().post("/users/signup/"),
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["referral_code"] == code.code


def test_manual_signup_rejects_an_invalid_optional_referral_code() -> None:
    form = CustomSignupForm(
        data={
            "email": "invitee@example.test",
            "username": "invitee",
            "password1": "Complex-Password-123!",
            "password2": "Complex-Password-123!",
            "referral_code": "INVALID!",
        },
        request=RequestFactory().post("/users/signup/"),
    )

    assert not form.is_valid()
    assert "referral_code" in form.errors


def test_social_onboarding_accepts_a_valid_optional_referral_code(
    referrer, make_user
) -> None:
    code = services.get_or_create_code(referrer)
    user = make_user("social_invitee")
    form = TellUsAboutYouForm(
        data={
            "username": "social_invitee",
            "full_name": "Social Invitee",
            "country": "US",
            "interests": ["firmware_download"],
            "referral_code": code.code,
        },
        user=user,
        request=RequestFactory().post("/users/tell-us-about-you/"),
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["referral_code"] == code.code
