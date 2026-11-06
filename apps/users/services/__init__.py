"""Shared exports for user-owned services."""

from .country_detection import (
    COUNTRY_PHONE_CODES,
    auto_detect_user_country,
    detect_country,
    get_phone_code_for_country,
    is_email_verified,
    is_profile_complete,
    requires_email_verification,
    validate_phone_number,
)
from .notifications import (
    broadcast_notification,
    notifications_enabled,
    send_notification,
)
from .rate_limit import allow_action, get_attempt_count, reset_rate_limit

__all__ = [
    "COUNTRY_PHONE_CODES",
    "allow_action",
    "auto_detect_user_country",
    "broadcast_notification",
    "detect_country",
    "get_attempt_count",
    "get_phone_code_for_country",
    "is_email_verified",
    "is_profile_complete",
    "notifications_enabled",
    "requires_email_verification",
    "reset_rate_limit",
    "send_notification",
    "validate_phone_number",
]
