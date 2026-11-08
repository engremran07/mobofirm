from __future__ import annotations

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
from apps.devices import slots
from apps.devices.models import Device, DeviceEvent
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    ensure_browser_key,
    get_device_registration_summary,
    hash_browser_key,
)


@login_required(login_url="users:account_login")
@require_GET
def my_devices(request: HttpRequest) -> HttpResponse:
    current_key_hash = hash_browser_key(ensure_browser_key(request))
    current_device = Device.objects.filter(
        user_id=request.user.pk,
        browser_key_hash=current_key_hash,
    ).first()
    summary = get_device_registration_summary(request.user)
    devices = list(
        Device.objects.filter(user_id=request.user.pk).order_by("-last_seen_at")
    )
    at_limit = summary["active_devices"] >= summary["max_devices"]
    can_approve_current = bool(
        current_device is not None
        and current_device.browser_key_hash
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
