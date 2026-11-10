"""``tell_us_about_you`` must save whatever legacy shape ``Profile.social_links`` holds.

Regression: POST ``/users/tell-us-about-you/?next=/marketplace/sell/`` raised
``TypeError: 'str' object is not a mapping`` when the stored value was a JSON
string, because the view spread it into a dict. The interests the user picks are
kept and the seller application flow that sends people here still completes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db.models import JSONField, Value
from django.urls import reverse
from django.utils import timezone

from apps.marketplace import services as marketplace_services
from apps.marketplace.models import (
    MAX_SOCIAL_LINKS,
    SellerProfile,
    SellerVerification,
)
from apps.users.profile_models import Profile
from apps.users.services.profile_completion import is_profile_complete

pytestmark = pytest.mark.django_db

SELL_URL = "/marketplace/sell/"
BIO = (
    "I repack and verify official MediaTek firmware and publish tested flashing notes."
)


def _member(username: str = "reseller", **extra: Any):
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="Reseller-pass-8431!",
        email_verified_at=timezone.now(),
        **extra,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


def _profile_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "username": "reseller",
        "full_name": "Re Seller",
        "country": "PK",
        "interests": ["marketplace", "firmware_download"],
        "next": SELL_URL,
    }
    data.update(overrides)
    return data


def _set_stored(profile: Profile, stored: Any) -> None:
    """Write ``stored`` exactly as legacy data would hold it (JSON ``null`` included)."""
    value = Value(None, output_field=JSONField()) if stored is None else stored
    Profile.objects.filter(pk=profile.pk).update(social_links=value)


LEGACY_SHAPES = {
    "empty dict": {},
    "dict with other keys": {"twitter": "https://x.com/me", "interests": ["old"]},
    "json string": json.dumps({"interests": ["old"], "links": ["a"]}),
    "double encoded string": json.dumps(json.dumps({"interests": ["old"]})),
    "plain string": "not json at all",
    "list": ["a", "b"],
    "json null": None,
}


@pytest.mark.parametrize("stored", LEGACY_SHAPES.values(), ids=LEGACY_SHAPES.keys())
def test_post_succeeds_and_keeps_interests_for_every_stored_shape(client, stored):
    user = _member()
    profile = Profile.objects.create(user=user)
    _set_stored(profile, stored)
    client.force_login(user)

    response = client.post(
        f"{reverse('users:tell_us_about_you')}?next={SELL_URL}", _profile_payload()
    )

    assert response.status_code == 302
    assert response["Location"] == SELL_URL
    profile.refresh_from_db()
    assert profile.social_links_data["interests"] == [
        "marketplace",
        "firmware_download",
    ]
    assert isinstance(profile.social_links, dict)
    user.refresh_from_db()
    assert user.profile_completed is True


def test_other_profile_keys_survive_the_merge(client):
    user = _member()
    Profile.objects.create(
        user=user, social_links={"twitter": "https://x.com/me", "interests": ["old"]}
    )
    client.force_login(user)

    client.post(reverse("users:tell_us_about_you"), _profile_payload())

    stored = Profile.objects.get(user=user).social_links_data
    assert stored["twitter"] == "https://x.com/me"
    assert stored["interests"] == ["marketplace", "firmware_download"]


def test_a_user_without_a_profile_row_can_post(client):
    user = _member()
    client.force_login(user)

    response = client.post(reverse("users:tell_us_about_you"), _profile_payload())

    assert response.status_code == 302
    assert Profile.objects.get(user=user).social_links_data["interests"]


@pytest.mark.parametrize("stored", LEGACY_SHAPES.values(), ids=LEGACY_SHAPES.keys())
def test_get_renders_for_every_stored_shape(client, stored):
    user = _member()
    _set_stored(Profile.objects.create(user=user), stored)
    client.force_login(user)

    response = client.get(reverse("users:tell_us_about_you"))

    assert response.status_code == 200


def _application_payload() -> dict[str, str]:
    data = {
        "bio": BIO,
        "website": "",
        "contact_preference": "social",
        "accept_terms": "on",
        "links-TOTAL_FORMS": str(MAX_SOCIAL_LINKS),
        "links-INITIAL_FORMS": "0",
        "links-MIN_NUM_FORMS": "0",
        "links-MAX_NUM_FORMS": str(MAX_SOCIAL_LINKS),
    }
    for index in range(MAX_SOCIAL_LINKS):
        data[f"links-{index}-platform"] = "github" if index == 0 else ""
        data[f"links-{index}-url"] = "https://github.com/reseller" if index == 0 else ""
    return data


def test_become_seller_flow_from_profile_completion_to_approval(client):
    """/marketplace/sell/ -> complete profile -> apply -> staff approves -> seller."""
    user = _member()
    # The legacy bad shape that used to crash the profile POST.
    _set_stored(
        Profile.objects.create(user=user), json.dumps({"interests": ["marketplace"]})
    )
    staff = _member("review_staff", is_staff=True, is_superuser=True)
    client.force_login(user)

    # 1. The seller page sends an incomplete profile to the completion page with next.
    gate = client.get(SELL_URL)
    assert gate.status_code == 302
    completion = gate["Location"]
    assert completion.startswith(reverse("users:tell_us_about_you"))
    assert "next=%2Fmarketplace%2Fsell%2F" in completion

    # 2. The completion form posts successfully and returns to the seller page.
    saved = client.post(completion, _profile_payload())
    assert saved.status_code == 302
    assert saved["Location"] == SELL_URL
    user.refresh_from_db()
    assert is_profile_complete(user)

    # 3. The application form is now shown, and submitting it redirects (PRG).
    assert client.get(SELL_URL).status_code == 200
    applied = client.post(SELL_URL, _application_payload())
    assert applied.status_code == 302
    assert applied["Location"] == reverse("marketplace:application_status")
    seller = SellerProfile.objects.get(user=user)
    assert seller.verified is False
    verification = SellerVerification.objects.get(seller=seller)
    assert verification.status == SellerVerification.Status.PENDING
    assert client.get(applied["Location"]).status_code == 200

    # 4. Staff approval makes the account an active seller and opens the dashboard.
    marketplace_services.review_verification(
        verification.pk, reviewer=staff, approve=True, reason="Looks good"
    )
    assert marketplace_services.is_active_seller(user)
    approved = client.get(SELL_URL)
    assert approved.status_code == 302
    assert approved["Location"] == reverse("marketplace:dashboard")
    assert client.get(approved["Location"]).status_code == 200
