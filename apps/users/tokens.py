"""
apps.users.tokens
Enterprise-safe token utilities for the platform.

✅ Highlights
-------------
• Cryptographically secure random tokens
• URL-safe output (for email links or QR codes)
• Timezone-aware expiry calculation
• Configurable length & lifetime
• No deprecated or unsafe modules
"""

from __future__ import annotations

import datetime
import secrets
from datetime import timedelta

from django.utils import timezone

# ============================================================
#  TOKEN GENERATION
# ============================================================


def generate_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure, URL-safe token.

    Args:
        length (int): Desired token length (default 32).
                      Must be ≥ 8 and ≤ 128 for best entropy/performance.

    Returns:
        str: Secure random token trimmed to desired length.
    """
    if not isinstance(length, int) or length < 8 or length > 128:
        length = 32
    token = secrets.token_urlsafe(length * 2)  # overshoot for trimming
    return token[:length]


# ============================================================
#  TOKEN EXPIRY UTILITIES
# ============================================================


def token_expiry(hours: int = 24) -> datetime.datetime:
    """
    Compute an expiry datetime for a token using Django's timezone utilities.

    Args:
        hours (int): Lifetime in hours (default 24).

    Returns:
        datetime: Timezone-aware expiry timestamp.
    """
    safe_hours = hours if isinstance(hours, (int, float)) and hours > 0 else 24
    return timezone.now() + timedelta(hours=safe_hours)


# ============================================================
#  TOKEN VALIDATION (Optional Utility)
# ============================================================


def is_token_expired(created_at: datetime.datetime, hours: int = 24) -> bool:
    """
    Determine whether a token has expired.

    Args:
        created_at (datetime): Original token creation timestamp.
        hours (int): Valid lifetime in hours (default 24).

    Returns:
        bool: True if expired, False otherwise.
    """
    if not created_at:
        return True
    expiry_time = created_at + timedelta(hours=hours)
    return timezone.now() >= expiry_time
