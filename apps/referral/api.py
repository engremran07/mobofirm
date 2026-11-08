"""Public contract of the referral app, reached through ``AppService.get("referral")``."""

from __future__ import annotations

from typing import Any

from apps.referral import services


def referral_code_for(user: Any) -> str:
    """The user's referral code (created on first use), or '' while referrals are off.

    Used by distribution to append ``?ref=<code>`` to shared links, so sign-ups that
    follow a share are credited to that account by the normal referral flow.
    """
    if user is None or not getattr(user, "pk", None):
        return ""
    if not services.get_settings().enabled:
        return ""
    return services.get_or_create_code(user).code
