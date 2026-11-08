"""Profile completion rules shared by onboarding, middleware and other apps.

A profile is complete once the account has a chosen username, a full name, a
country and at least one interest, and the onboarding flag was confirmed by
``users:tell_us_about_you``. Other apps (for example marketplace seller
onboarding) call ``is_profile_complete`` and send the user to
``profile_completion_url`` when it is false.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from django.http import HttpRequest
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.users.profile_models import Profile
from apps.users.reward_events import PROFILE_COMPLETED_EVENT
from apps.wallet import api as wallet_api

if TYPE_CHECKING:
    from apps.users.models import CustomUser

REQUIRED_PROFILE_FIELDS: Final[tuple[str, ...]] = (
    "username",
    "full_name",
    "country",
    "interests",
)
# Reported when every field is filled but onboarding was never confirmed.
CONFIRMATION_FIELD: Final[str] = "profile_completed"
PROFILE_COMPLETION_URL_NAME: Final[str] = "users:tell_us_about_you"


def profile_interests(user: CustomUser) -> list[str]:
    """Return the interests stored on the user's profile."""
    links = (
        Profile.objects.filter(user=user).values_list("social_links", flat=True).first()
    )
    if not isinstance(links, dict):
        return []
    interests = links.get("interests")
    if not isinstance(interests, list):
        return []
    return [str(item) for item in interests if item]


def missing_profile_fields(user: CustomUser) -> list[str]:
    """Names of the onboarding fields the user still has to provide."""
    missing: list[str] = []
    username = (user.username or "").strip()
    if not username or username.casefold() == (user.email or "").strip().casefold():
        missing.append("username")
    if not (user.full_name or "").strip():
        missing.append("full_name")
    if not (user.country or "").strip():
        missing.append("country")
    if not profile_interests(user):
        missing.append("interests")
    if not missing and not user.profile_completed:
        missing.append(CONFIRMATION_FIELD)
    return missing


def is_profile_complete(user: CustomUser) -> bool:
    """True when the user has finished onboarding and every field is filled."""
    return bool(user.profile_completed) and not missing_profile_fields(user)


def safe_next_url(request: HttpRequest, candidate: str | None) -> str:
    """Return ``candidate`` when it is a same-site URL, otherwise an empty string."""
    value = (candidate or "").strip()
    if not value or any(char in value for char in "\r\n\\"):
        return ""
    allowed = url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    )
    return value if allowed else ""


def profile_completion_url(next_url: str = "") -> str:
    """URL of the completion page, carrying ``next`` when one is given."""
    base = reverse(PROFILE_COMPLETION_URL_NAME)
    return f"{base}?{urlencode({'next': next_url})}" if next_url else base


def reward_profile_completion(user: CustomUser) -> None:
    """Pay the one-time ``profile.completed`` reward once the profile is complete.

    Idempotent on ``profile:<user id>``; the wallet applies its rule, caps and
    verified-email check and records a reason when it declines.
    """
    if not is_profile_complete(user):
        return
    key = f"profile:{user.pk}"
    wallet_api.grant_reward(user, PROFILE_COMPLETED_EVENT, key=key, reference=key)
