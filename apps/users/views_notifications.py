from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import Notification
from .request_user import current_user

logger = logging.getLogger(__name__)


def _safe_referer(request: HttpRequest) -> str:
    """Return the Referer only when it points back at this site."""
    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return referer
    return reverse("users:notifications")


# ============================================================================
# Serializers (JSON Safe)
# ============================================================================
def _serialize_notification(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "title": getattr(n, "title", ""),
        "message": getattr(n, "message", ""),
        "priority": getattr(n, "priority", None),
        "channel": getattr(n, "channel", None),
        "url": getattr(n, "url", None),
        "is_read": bool(n.is_read),
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


# ============================================================================
# HTML Detail Page
# ============================================================================
@login_required
@require_http_methods(["GET", "POST"])
def notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """
    HTML detail page for a single notification.
    Name fixed to match notifications_urls.py import.
    """
    notif = get_object_or_404(Notification, pk=pk, recipient=request.user)

    if request.method == "POST":
        _mark_notification_read(notif)
        return redirect("users:notification_detail", pk=notif.pk)

    return render(
        request,
        "users/notifications/details.html",
        {"notification": notif},
    )


def _mark_notification_read(notif: Notification) -> bool:
    return bool(
        Notification.objects.filter(
            pk=notif.pk, recipient=notif.recipient, is_read=False
        ).update(is_read=True, read_at=timezone.now())
    )


# ============================================================================
# HTML List Page
# ============================================================================
@login_required
@require_GET
def notification_list(request: HttpRequest) -> HttpResponse:
    scope = (request.GET.get("scope") or "all").lower()
    qs = Notification.objects.filter(recipient=current_user(request)).order_by(
        "-created_at"
    )
    if scope == "unread":
        qs = qs.filter(is_read=False)
    return render(
        request,
        "users/notifications/list.html",
        {"notifications": qs, "scope": scope},
    )


# ============================================================================
# JSON Endpoints
# ============================================================================
@login_required
@require_GET
def notification_unread_count(request: HttpRequest) -> JsonResponse:
    count = Notification.objects.filter(
        recipient=current_user(request), is_read=False
    ).count()
    return JsonResponse({"ok": True, "unread_count": count})


@login_required
@require_POST
def notification_mark_read(request: HttpRequest, pk: int) -> HttpResponse:
    notif = get_object_or_404(Notification, pk=pk, recipient=current_user(request))
    _mark_notification_read(notif)

    if "application/json" in (
        request.headers.get("Accept") or ""
    ) or request.headers.get("HX-Request"):
        return JsonResponse({"ok": True})
    return redirect(_safe_referer(request))


@login_required
@require_POST
def notification_mark_all_read(request: HttpRequest) -> HttpResponse:
    Notification.objects.filter(recipient=current_user(request), is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    if "application/json" in (
        request.headers.get("Accept") or ""
    ) or request.headers.get("HX-Request"):
        return JsonResponse({"ok": True})
    return redirect(_safe_referer(request))
