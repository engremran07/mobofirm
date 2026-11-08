"""
apps/users/services/country_detection.py

User-related services including:
- Edge-header country detection (no third-party IP lookups)
- Phone number validation
- Email verification state (profile completion lives in profile_completion.py)
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.core.geo import COUNTRY_PHONE_CODES

logger = logging.getLogger(__name__)


def get_phone_code_for_country(country_code: str) -> str:
    """Get phone country code for a given ISO country code."""
    return COUNTRY_PHONE_CODES.get(country_code.upper(), "")


def detect_country(request) -> str | None:
    """
    Read the visitor's country from the edge proxy/CDN header, if configured.

    No visitor IP is sent to a third party and no network call is made. The
    header named by ``COUNTRY_HEADER`` (for example ``CF-IPCountry``) is only
    honoured when ``TRUSTED_PROXY_COUNT`` marks the app as sitting behind a
    proxy that overwrites it. Returns an ISO 3166-1 alpha-2 code or None.
    """
    header = (getattr(settings, "COUNTRY_HEADER", "") or "").strip()
    if not header or int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0) <= 0:
        return None
    meta_key = "HTTP_" + header.upper().replace("-", "_")
    code = (request.META.get(meta_key) or "").strip().upper()
    if len(code) == 2 and code.isalpha() and code not in {"XX", "T1"}:
        return code
    return None


def auto_detect_user_country(user, request) -> bool:
    """
    Auto-detect and set user's country from the trusted edge header.

    Returns True if country was detected and set.
    """
    if user.country and user.country_detected_at:
        # Already detected
        return False

    country_code = detect_country(request)

    if country_code:
        user.country = country_code
        user.country_detected_at = timezone.now()

        # Also set phone country code if not set
        if not user.phone_country_code:
            user.phone_country_code = get_phone_code_for_country(country_code)

        user.save(
            update_fields=["country", "country_detected_at", "phone_country_code"]
        )
        logger.info("Auto-detected country %s for user %s", country_code, user.pk)
        return True

    return False


def is_email_verified(user) -> bool:
    """Check if user's email is verified."""
    # Social login users are considered verified
    if user.signup_method == "social":
        return True

    # Manual signup users need explicit verification
    return user.email_verified_at is not None


def requires_email_verification(user) -> bool:
    """
    Check if user needs to verify email before sensitive actions.

    Social login users skip this (already verified by provider).
    """
    if user.signup_method == "social":
        return False

    return user.email_verified_at is None


def validate_phone_number(phone: str) -> tuple[bool, str]:
    """
    Validate phone number format.

    Returns (is_valid, error_message)
    """
    if not phone:
        return True, ""

    # Remove all non-digit characters except +
    cleaned = re.sub(r"[^\d+]", "", phone)

    # Must start with + or be digits only
    if not re.match(r"^\+?\d{7,15}$", cleaned):
        return False, "Invalid phone number format"

    return True, ""
