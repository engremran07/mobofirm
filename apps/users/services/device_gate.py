"""
apps.users.services.device_gate
-------------------------------
Users' single seam to the optional Devices policy contract.

Every way of becoming signed in asks this module whether the device policy allows
it, so no login path carries its own copy of the call, the failure handling or the
wording shown to the person:

* web password sign-in (``EnterpriseLoginView``) and its MFA completion,
* every allauth login (social callback, email-confirmation link and code,
  sign-up) through ``CustomAccountAdapter.login``, allauth's single point of
  session establishment,
* API token issuance (password and Google).

The policy itself belongs to ``apps.devices``; it is reached only through
``AppService`` (``enforce_device_policy_for_login`` and ``DevicePolicyError``). When
the devices app is not installed there is nothing to enforce and every sign-in is
allowed. When it is installed but the check cannot be completed the sign-in is
refused (fail closed): a policy error never becomes a silent pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

from django.apps import apps
from django.db import DatabaseError
from django.http import HttpRequest

from apps.core.app_service import AppService
from apps.users.services.rate_limit import CACHE_ERRORS

logger = logging.getLogger(__name__)

#: Policy reasons that a second factor can satisfy.
MFA_REASONS: Final[frozenset[str]] = frozenset({"mfa_required", "mfa_required_risk"})

#: Reason reported when the optional devices contract could not be consulted.
UNAVAILABLE: Final[str] = "device_check_unavailable"

#: Marks a request whose caller applies the device policy itself.
_APPLIED_ATTR: Final[str] = "users_device_gate_applied"

# Failures the optional devices contract may raise while checking a sign-in.
_CHECK_ERRORS: Final[tuple[type[Exception], ...]] = (
    DatabaseError,
    *CACHE_ERRORS,
    AttributeError,
    LookupError,
    RuntimeError,
    TypeError,
    ValueError,
)

_MESSAGES: Final[dict[str, str]] = {
    "blocked_device": "This device is blocked. Contact support to unblock it.",
    "device_key_required": "This device could not be identified. Try again.",
    "untrusted_new_device": (
        "This new device has not been approved. Use an existing trusted session "
        "to approve it or contact support."
    ),
    "revoked_device": (
        "This device registration was revoked. Contact an administrator."
    ),
    "mfa_required": (
        "This device requires MFA, but verification is not currently available. "
        "Contact support."
    ),
    "mfa_required_risk": (
        "This device requires additional security verification, which is not "
        "currently available. Contact support."
    ),
    "monthly_device_quota": (
        "The monthly device limit has been reached. Contact an administrator."
    ),
    "yearly_device_quota": (
        "The yearly device limit has been reached. Contact an administrator."
    ),
    "user_window_quota": (
        "The device enrollment limit has been reached. Contact an administrator."
    ),
    "device_quota_exceeded": (
        "The maximum number of devices has been reached. Contact an administrator."
    ),
    "limit_reached": (
        "The maximum number of devices has been reached. Contact an administrator "
        "to reset or revoke a registration."
    ),
    "fallback_identity_blocked": ("Device identity could not be verified. Try again."),
    UNAVAILABLE: "Device security check could not be completed. Try again later.",
}
_DEFAULT_MESSAGE: Final[str] = "This device is not allowed to sign in. Contact support."


@dataclass(frozen=True, slots=True)
class DeviceGateDecision:
    """Outcome of one device-policy check for a sign-in."""

    allowed: bool
    reason: str = ""
    #: The enforcement context of an allowed check (``device``, ``is_new``, ...).
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def mfa_required(self) -> bool:
        """True when the policy refused only because no second factor was verified."""
        return not self.allowed and self.reason in MFA_REASONS

    @property
    def unavailable(self) -> bool:
        """True when the check itself failed (the sign-in is refused)."""
        return not self.allowed and self.reason == UNAVAILABLE

    @property
    def message(self) -> str:
        """Plain-language explanation for the person who was refused."""
        return denial_message(self.reason)


def denial_message(reason: str) -> str:
    """The wording shown when the device policy refuses a sign-in."""
    return _MESSAGES.get(reason, _DEFAULT_MESSAGE)


def mark_device_gate_applied(request: HttpRequest) -> None:
    """Record that the caller applies the device policy itself for this request.

    The allauth hook (``CustomAccountAdapter.pre_login``) then skips its own check,
    so a sign-in is evaluated once: the password form has just evaluated it, and the
    API evaluates it after the second factor.
    """
    setattr(request, _APPLIED_ATTR, True)


def device_gate_applied(request: HttpRequest) -> bool:
    """Whether ``mark_device_gate_applied`` was called for this request."""
    return bool(getattr(request, _APPLIED_ATTR, False))


def _contract_member(contract: object, name: str) -> Any:
    """A public export of the devices contract, or ``None`` when it is missing."""
    return getattr(contract, name, None)


def evaluate_device_policy(
    request: HttpRequest, user: Any, *, mfa_verified: bool = False
) -> DeviceGateDecision:
    """Ask the devices contract whether ``user`` may sign in from this request."""
    if not apps.is_installed("apps.devices"):
        return DeviceGateDecision(allowed=True)

    contract = AppService.get("devices")
    enforce = _contract_member(contract, "enforce_device_policy_for_login")
    if not callable(enforce):
        logger.error("Device login policy contract is unavailable")
        return DeviceGateDecision(allowed=False, reason=UNAVAILABLE)
    policy_error = _contract_member(contract, "DevicePolicyError") or ()
    try:
        allowed, context = enforce(request, user, mfa_verified=mfa_verified)
    except policy_error as exc:
        return DeviceGateDecision(
            allowed=False, reason=str(getattr(exc, "reason", "policy_denied"))
        )
    except _CHECK_ERRORS:
        logger.exception("Device policy check failed for user_id=%s", user.pk)
        return DeviceGateDecision(allowed=False, reason=UNAVAILABLE)
    if not allowed:
        reason = str((context or {}).get("reason", "policy_denied"))
        return DeviceGateDecision(allowed=False, reason=reason)
    return DeviceGateDecision(allowed=True, context=context or {})


def device_registration_prompt(
    enforcement_ctx: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Session payload asking the user to register a new, untrusted device."""
    if not enforcement_ctx:
        return None
    device = enforcement_ctx.get("device")
    if not (
        device
        and not device.is_trusted
        and not device.is_blocked
        and device.revoked_at is None
    ):
        return None
    context = enforcement_ctx.get("context", {})
    active_devices = context.get("current_device_count", 0)
    max_devices = context.get("max_devices", 3)
    return {
        "device_name": f"{device.os_family} device"
        if device.os_family
        else "New device",
        "os_family": device.os_family,
        "active_devices": active_devices,
        "max_devices": max_devices,
    }
