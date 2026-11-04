from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from apps.devices.services import (
    DevicePolicyError,
    attach_device_cookie,
    enforce_device_policy_for_service,
)


class DeviceEnforcementMiddleware:
    """
    Enforce device resolution and policy on every authenticated request.
    Attaches request.device and request.device_new.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip unauthenticated requests
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return self.get_response(request)

        # Respect registry toggle if device identity is disabled
        try:
            from apps.core.models import AppRegistry

            reg = AppRegistry.get_solo()
            if reg and getattr(reg, "device_identity_enabled", True) is False:
                return self.get_response(request)
        except Exception:  # noqa: S110
            pass

        # Skip for trust/untrust endpoints to avoid consent/fp prompts
        # CRITICAL: Must include the actual approval action URL, otherwise we get an infinite loop
        # because the approval view itself triggers the "untrusted device" check before it can run.
        if (
            request.path.startswith(reverse("users:device_approval_needed"))
            or request.path.startswith(reverse("users:approve_device"))
            or request.path.startswith(reverse("users:device_mfa_challenge"))
            or request.path.startswith(reverse("devices:acknowledge_new_device"))
        ):
            return self.get_response(request)

        try:
            _ok, result = enforce_device_policy_for_service(
                request, request.user, service_name="request"
            )
            request.device = result.get("device")
            request.device_new = result.get("is_new", False)
            inner_ctx = result.get("context", {})

            response = self.get_response(request)
            attach_device_cookie(response, result.get("device"))  # type: ignore[arg-type]

            # Update popup data if it exists (to ensure counts are fresh)
            if request.session.get("new_device_popup"):
                try:
                    device_obj = result.get("device")
                    # Force raw fingerprint update
                    dev_name_update = getattr(
                        device_obj, "display_name", ""
                    ) or getattr(device_obj, "os_fingerprint", "New Device")

                    request.session["new_device_popup"].update(
                        {
                            "device_name": dev_name_update,
                            "remaining_devices": inner_ctx.get("remaining_devices"),
                            "current_count": inner_ctx.get("current_device_count"),
                            "max_devices": inner_ctx.get("max_devices"),
                            "quota_reset_days": inner_ctx.get("quota_reset_days"),
                        }
                    )
                    request.session.modified = True
                except Exception:  # noqa: S110
                    pass

            # Only show popup for NEW devices that are NOT already trusted
            # This prevents the popup from reappearing after user acknowledges
            device = result.get("device")
            device_fp = getattr(device, "os_fingerprint", "") if device else ""

            # Track which devices have been shown the popup to prevent re-showing
            # after user dismisses it (even if they didn't accept)
            shown_devices = set(request.session.get("devices_popup_shown", []))

            if result.get("is_new") and device_fp:
                # Check if device is already trusted - if so, don't show popup
                if device and getattr(device, "is_trusted", False):
                    # Device is trusted, clear any stale popup data
                    if "new_device_popup" in request.session:
                        del request.session["new_device_popup"]
                    if "pending_device_prompt_uuid" in request.session:
                        del request.session["pending_device_prompt_uuid"]
                    # Mark this device as having been shown
                    shown_devices.add(device_fp)
                    request.session["devices_popup_shown"] = list(shown_devices)
                    request.session.modified = True
                elif device_fp not in shown_devices:
                    # Only show popup if we haven't shown it for this device before
                    try:
                        # Use raw fingerprint as requested
                        dev_name = getattr(device, "display_name", "") or getattr(
                            device, "os_fingerprint", "New Device"
                        )

                        # Store data for the new device popup
                        request.session["new_device_popup"] = {
                            "device_name": dev_name,
                            "os_fingerprint": device_fp,
                            "remaining_devices": inner_ctx.get("remaining_devices"),
                            "current_count": inner_ctx.get("current_device_count"),
                            "max_devices": inner_ctx.get("max_devices"),
                            "quota_reset_days": inner_ctx.get("quota_reset_days"),
                        }
                        request.session["pending_device_prompt_uuid"] = device_fp
                    except Exception:  # noqa: S110
                        pass
            return response
        except DevicePolicyError as exc:
            reason = exc.reason
            if reason == "device_key_required":
                messages.error(
                    request,
                    "Device fingerprint is required. Refresh the page to register this device.",
                )
                return redirect("users:devices")
            if reason in {"untrusted_new_device", "mfa_required", "mfa_required_risk"}:
                messages.error(request, "New device requires approval or MFA.")
                return redirect("users:device_approval_needed")
            if reason in {
                "device_quota_exceeded",
                "limit_reached",
                "user_window_quota",
                "monthly_device_quota",
                "yearly_device_quota",
            }:
                messages.error(
                    request, "Device limit reached. Remove an old device to continue."
                )
                return redirect("users:device_eviction")
            if reason == "blocked_device":
                messages.error(request, "This device is blocked. Contact support.")
                return redirect("users:devices")
            messages.error(request, "Device not allowed for this action.")
            return redirect("users:devices")


class DevicePayloadMiddleware:
    """
    Attach device payload captured via JS to the request for downstream fingerprinting.
    Reads from session first, then falls back to cookie.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import json
        import urllib.parse

        # 1. Try session
        payload = request.session.get("device_payload")

        # 2. Fallback to cookie if session is empty
        if not payload:
            cookie_val = request.COOKIES.get("device_payload")
            if cookie_val:
                try:
                    # Cookie is URI encoded in JS
                    decoded = urllib.parse.unquote(cookie_val)
                    payload = json.loads(decoded)
                    # Optional: Promote to session for faster access next time
                    # request.session["device_payload"] = payload
                except Exception:
                    payload = None

        request.device_payload = payload
        return self.get_response(request)
