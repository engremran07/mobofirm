from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model

User = get_user_model()


def get_user_profile(user_id: str) -> dict[str, Any]:
    """
    Aggregate user profile data for admin views:
    - CustomUser base fields
    - allauth EmailAddress + SocialAccount (if installed)
    - Devices + recent events
    - Behavior insights (risk)
    """
    profile: dict[str, Any] = {
        "user": None,
        "email_addresses": [],
        "social_accounts": [],
        "devices": [],
        "device_events": [],
        "risk_insights": [],
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
    except Exception:
        profile["email_addresses"] = []

    # allauth: social accounts
    try:
        from allauth.socialaccount.models import SocialAccount

        profile["social_accounts"] = list(
            SocialAccount.objects.filter(user=user)
            .order_by("provider")
            .values("provider", "uid", "last_login")
        )
    except Exception:
        profile["social_accounts"] = []

    # Devices + events
    try:
        from apps.devices.models import Device, DeviceEvent

        profile["devices"] = list(
            Device.objects.filter(user=user)
            .order_by("-last_seen_at")
            .values(
                "os_fingerprint",
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
    except Exception:
        profile["devices"] = []
        profile["device_events"] = []

    # Risk insights
    try:
        from apps.devices.models import BehaviorInsight

        profile["risk_insights"] = list(
            BehaviorInsight.objects.filter(related_user_id=user.pk)
            .order_by("-created_at")[:20]
            .values(
                "severity",
                "status",
                "device_identifier",
                "related_ip",
                "created_at",
            )
        )
    except Exception:
        profile["risk_insights"] = []

    return profile
