from __future__ import annotations

import json
from typing import Any

from allauth.account.models import EmailAddress
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.core.admin_pages import render_admin_page
from apps.devices import os_identity, slots
from apps.devices.models import Device, DeviceEvent
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    find_current_device,
    get_device_registration_summary,
)
from apps.users.services.rate_limit import allow_action

OS_ACTIONS_PER_WINDOW = 30
OS_ACTION_WINDOW_SECONDS = 600
_MAX_JSON_BYTES = 16384

_OS_ERROR_STATUS = {
    "malformed": 400,
    "challenge_missing": 400,
    "challenge_expired": 400,
    "challenge_mismatch": 400,
    "wrong_type": 400,
    "origin_not_allowed": 403,
    "rp_id_mismatch": 403,
    "user_not_present": 403,
    "bad_signature": 403,
    "unsupported_algorithm": 400,
    "credential_mismatch": 400,
    "credential_conflict": 409,
    "unknown_credential": 403,
    "sign_count_regressed": 403,
    "blocked_device": 403,
    "revoked_device": 403,
    "no_credentials": 404,
    "disabled": 404,
}
_OS_ERROR_MESSAGES = {
    "challenge_missing": "This check expired. Try again.",
    "challenge_expired": "This check expired. Try again.",
    "challenge_mismatch": "This check expired. Try again.",
    "origin_not_allowed": "This site address cannot be used for computer registration.",
    "rp_id_mismatch": "This computer was registered under a different site address.",
    "bad_signature": "This computer could not be verified.",
    "unknown_credential": "This computer is not registered to your account.",
    "sign_count_regressed": "This computer could not be verified.",
    "blocked_device": "This device is blocked. Contact an administrator.",
    "revoked_device": "This device registration was revoked. Contact an administrator.",
    "no_credentials": "No computer is registered to your account yet.",
    "disabled": "Computer registration is not enabled.",
    "limit_reached": "Too many pending device registrations. Contact an administrator.",
    "monthly_device_quota": "The monthly device registration quota is reached.",
    "yearly_device_quota": "The yearly device registration quota is reached.",
}


@login_required(login_url="users:account_login")
@require_GET
def my_devices(request: HttpRequest) -> HttpResponse:
    current_device = find_current_device(request, request.user)
    summary = get_device_registration_summary(request.user)
    devices = list(
        Device.objects.filter(user_id=request.user.pk).order_by("-last_seen_at")
    )
    at_limit = summary["active_devices"] >= summary["max_devices"]
    can_approve_current = bool(
        current_device is not None
        and (current_device.browser_key_hash or current_device.os_credential_id_hash)
        and not current_device.is_trusted
        and not current_device.is_blocked
        and current_device.revoked_at is None
        and not at_limit
    )
    return render(
        request,
        "devices/my_devices.html",
        {
            "devices": devices,
            "current_device": current_device,
            "options": slots.slot_options(request.user) if at_limit else None,
            "can_approve_current": can_approve_current,
            **summary,
        },
    )


@staff_member_required(login_url="admin:login")
def device_events(request: HttpRequest) -> HttpResponse:
    """Recent device audit events, rendered inside the admin shell."""
    if not request.user.has_perm("devices.view_deviceevent"):
        raise PermissionDenied
    kinds = dict(DeviceEvent.EVENT_CHOICES)
    selected = request.GET.get("type", "")
    events = DeviceEvent.objects.select_related("device", "user").order_by(
        "-created_at"
    )
    if selected in kinds:
        events = events.filter(event_type=selected)
    return render_admin_page(
        request,
        "devices/events.html",
        title="Device events",
        subtitle="The latest 50 device and slot activity records.",
        context={
            "events": events[:50],
            "kinds": kinds,
            "selected": selected if selected in kinds else "",
        },
    )


# ---------------------------------------------------------------------
# Device payload API (collect JS fingerprint and store in session)
# ---------------------------------------------------------------------


@require_POST
@csrf_protect
def device_payload_view(request: HttpRequest) -> JsonResponse:
    """Retired endpoint: device registration no longer collects JS fingerprints."""
    return JsonResponse(
        {
            "ok": False,
            "error": "Browser-trait fingerprint collection is disabled.",
        },
        status=410,
    )


@require_POST
@csrf_protect
@login_required
def acknowledge_new_device(request: HttpRequest) -> HttpResponse:
    """Approve or defer the browser profile represented by this request's cookie."""
    decision = request.POST.get("decision", "approve")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = ""

    if decision == "later":
        request.session.pop("device_registration_prompt", None)
        messages.info(
            request, "This browser remains unregistered until you approve it."
        )
        return redirect(next_url or "users:dashboard")
    if decision != "approve":
        return JsonResponse({"ok": False, "error": "Invalid decision."}, status=400)

    try:
        approve_current_device(request, request.user)
    except DevicePolicyError as exc:
        if exc.reason == "limit_reached":
            messages.error(
                request,
                "Your approved-device allowance is full. Free a slot below: ask "
                "staff, pay credits or watch a rewarded ad.",
            )
        elif exc.reason == "blocked_device":
            messages.error(
                request, "This browser is blocked. Contact an administrator."
            )
        elif exc.reason == "revoked_device":
            messages.error(
                request,
                "This browser registration was revoked. Contact an administrator.",
            )
        else:
            messages.error(request, "This browser registration could not be approved.")
        return redirect(next_url or "devices:my_devices")

    request.session.pop("device_registration_prompt", None)
    messages.success(request, "This browser profile is now registered to your account.")
    return redirect(next_url or "users:dashboard")


# ---------------------------------------------------------------------
# Operating-system registration (platform WebAuthn credential)
# ---------------------------------------------------------------------


def _os_error(reason: str) -> JsonResponse:
    return JsonResponse(
        {
            "ok": False,
            "error": reason,
            "message": _OS_ERROR_MESSAGES.get(
                reason, "This computer could not be registered. Try again."
            ),
        },
        status=_OS_ERROR_STATUS.get(reason, 400),
    )


def _email_verified(user: Any) -> bool:
    """Verified email by the account flag, social sign-up, or a verified allauth address."""
    return bool(
        getattr(user, "email_verified_at", None)
        or getattr(user, "signup_method", "") == "social"
        or EmailAddress.objects.filter(user=user, verified=True).exists()
    )


def _os_request_payload(
    request: HttpRequest,
) -> tuple[dict[str, Any], JsonResponse | None]:
    """Gate + parse an OS-identity JSON call; the error response is returned, not raised."""
    user = request.user
    if not user.is_authenticated:
        return {}, JsonResponse({"ok": False, "error": "login_required"}, status=401)
    if not _email_verified(user):
        return {}, JsonResponse(
            {"ok": False, "error": "email_not_verified"}, status=403
        )
    if not os_identity.os_identity_enabled():
        return {}, _os_error("disabled")
    if not allow_action(
        f"devices:os-identity:{user.pk}",
        max_attempts=OS_ACTIONS_PER_WINDOW,
        window_seconds=OS_ACTION_WINDOW_SECONDS,
        fail_open=False,
    ):
        return {}, JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    if len(request.body) > _MAX_JSON_BYTES:
        return {}, _os_error("malformed")
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return {}, _os_error("malformed")
    if not isinstance(payload, dict):
        return {}, _os_error("malformed")
    return payload, None


@require_POST
@csrf_protect
def os_options(request: HttpRequest) -> JsonResponse:
    """Issue a single-use challenge and the WebAuthn options for a ceremony."""
    payload, failure = _os_request_payload(request)
    if failure is not None:
        return failure
    try:
        options = os_identity.build_options(
            request, request.user, str(payload.get("purpose", ""))
        )
    except os_identity.OsIdentityError as exc:
        return _os_error(exc.reason)
    return JsonResponse({"ok": True, "publicKey": options})


@require_POST
@csrf_protect
def os_register(request: HttpRequest) -> JsonResponse:
    """Register this operating system from a freshly created platform credential."""
    payload, failure = _os_request_payload(request)
    if failure is not None:
        return failure
    try:
        result = os_identity.register_os_credential(request, request.user, payload)
    except os_identity.OsIdentityError as exc:
        return _os_error(exc.reason)
    if result.approved:
        request.session.pop("device_registration_prompt", None)
        message = "This computer is now registered to your account."
        messages.success(request, message)
    else:
        message = (
            "This computer was recorded, but your approved-device allowance is "
            "full. Free a slot, then approve it from your devices page."
        )
        messages.error(request, message)
    return JsonResponse(
        {
            "ok": True,
            "created": result.created,
            "approved": result.approved,
            "limit_reached": result.limit_reached,
            "message": message,
        }
    )


@require_POST
@csrf_protect
def os_verify(request: HttpRequest) -> JsonResponse:
    """Recognise this browser as running on an already registered operating system."""
    payload, failure = _os_request_payload(request)
    if failure is not None:
        return failure
    try:
        device = os_identity.verify_os_credential(request, request.user, payload)
    except os_identity.OsIdentityError as exc:
        return _os_error(exc.reason)
    if device.is_trusted:
        request.session.pop("device_registration_prompt", None)
        message = "This computer is recognised. No new registration is needed."
        messages.success(request, message)
    else:
        message = (
            "This computer is recognised but not approved yet. Approve it to use it."
        )
        messages.info(request, message)
    return JsonResponse({"ok": True, "approved": device.is_trusted, "message": message})
