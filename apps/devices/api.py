from __future__ import annotations

from apps.devices.erasure import anonymise_user
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    enforce_device_policy_for_login,
    enforce_device_policy_for_service,
    ensure_browser_key,
    get_active_device_count,
    get_device_limit,
    get_device_registration_summary,
    hash_browser_key,
    resolve_identity,
)

__all__ = [
    "DevicePolicyError",
    "anonymise_user",
    "approve_current_device",
    "enforce_device_policy_for_login",
    "enforce_device_policy_for_service",
    "ensure_browser_key",
    "get_active_device_count",
    "get_device_limit",
    "get_device_registration_summary",
    "hash_browser_key",
    "resolve_identity",
]
