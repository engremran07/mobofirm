"""Profile ``social_links`` must stay usable even when stored as a JSON string."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model

from apps.users.profile_models import Profile
from apps.users.services.profile_completion import profile_interests

pytestmark = pytest.mark.django_db


@pytest.fixture(name="member")
def member_fixture():
    """A verified member with an empty profile."""
    return get_user_model().objects.create_user(
        username="linker", email="linker@example.test"
    )


def test_a_string_value_is_read_as_a_dict(member):
    payload = {"interests": ["firmware_download", "bounty_hunting"]}
    profile = Profile.objects.create(user=member, social_links=json.dumps(payload))

    assert profile.social_links_data == payload
    assert profile_interests(member) == payload["interests"]


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", "42", []])
def test_unusable_values_read_as_empty(member, stored):
    profile = Profile.objects.create(user=member, social_links=stored)

    assert profile.social_links_data == {}
    assert profile_interests(member) == []


def test_a_dict_value_is_returned_as_a_copy(member):
    profile = Profile.objects.create(user=member, social_links={"links": ["a"]})

    data = profile.social_links_data
    data["extra"] = True

    assert "extra" not in profile.social_links
