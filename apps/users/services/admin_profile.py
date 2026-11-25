from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db import DatabaseError

User = get_user_model()


def get_user_profile(user_id: str) -> dict[str, Any]:
    """
    Aggregate user profile data for admin views:
    - CustomUser base fields
    - allauth EmailAddress + SocialAccount (if installed)
    - Devices + recent audit events
    """
    profile: dict[str, Any] = {
        "user": None,
        "email_addresses": [],
        "social_accounts": [],
        "devices": [],
        "device_events": [],
    }

    user = User.objects.filter(pk=user_id).first()
    if not user:
        return profile
    profile["user"] = user

    # allauth: email addresses
    try:
        from allauth.account.models import EmailAddress

        profile["email_addresses"] = list(
            EmailAddress.objects.filter(user=user).values(
                "email", "verified", "primary"
            )
        )
    except DatabaseError:
        profile["email_addresses"] = []

    # allauth: social accounts
    try:
        from allauth.socialaccount.models import SocialAccount

        profile["social_accounts"] = list(
            SocialAccount.objects.filter(user=user)
            .order_by("provider")
            .values("provider", "uid", "last_login")
        )
    except DatabaseError:
        profile["social_accounts"] = []

    # Devices + events
    try:
        from apps.devices.models import Device, DeviceEvent

        profile["devices"] = list(
            Device.objects.filter(user=user)
            .order_by("-last_seen_at")
            .values(
                "display_name",
                "os_family",
                "browser_family",
                "is_blocked",
                "is_trusted",
                "last_seen_at",
                "risk_score",
            )
        )
        profile["device_events"] = list(
            DeviceEvent.objects.filter(user=user)
            .order_by("-created_at")[:20]
            .values("event_type", "success", "reason", "created_at")
        )
    except DatabaseError:
        profile["devices"] = []
        profile["device_events"] = []

    return profile
