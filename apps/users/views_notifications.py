from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Notification

logger = logging.getLogger(__name__)


# ============================================================================
# Serializers (JSON Safe)
# ============================================================================
def _serialize_notification(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,  # type: ignore[attr-defined]
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
@require_GET
def notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """
    HTML detail page for a single notification.
    Name fixed to match notifications_urls.py import.
    """
    notif = get_object_or_404(Notification, pk=pk, recipient=request.user)

    # Auto-mark as read
    if not notif.is_read:
        notif.is_read = True
        notif.read_at = timezone.now()
        notif.save(update_fields=["is_read", "read_at"])

    return render(
        request,
        "users/notifications/details.html",
        {"notification": notif},
    )


# ============================================================================
# HTML List Page
# ============================================================================
@login_required
@require_GET
def notification_list(request: HttpRequest) -> HttpResponse:
    scope = (request.GET.get("scope") or "all").lower()
    qs = Notification.objects.filter(recipient=request.user).order_by("-created_at")
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
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({"ok": True, "unread_count": count})


@login_required
@require_POST
def notification_mark_read(request: HttpRequest, pk: int) -> JsonResponse:
    notif = get_object_or_404(Notification, pk=pk, recipient=request.user)

    if not notif.is_read:
        notif.is_read = True
        notif.read_at = timezone.now()
        notif.save(update_fields=["is_read", "read_at"])

    if "application/json" in (
        request.headers.get("Accept") or ""
    ) or request.headers.get("HX-Request"):
        return JsonResponse({"ok": True})
    return redirect(request.META.get("HTTP_REFERER") or "users:notifications")  # type: ignore[return-value]


@login_required
@require_POST
def notification_mark_all_read(request: HttpRequest) -> JsonResponse:
    Notification.objects.filter(recipient=request.user, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    if "application/json" in (
        request.headers.get("Accept") or ""
    ) or request.headers.get("HX-Request"):
        return JsonResponse({"ok": True})
    return redirect(request.META.get("HTTP_REFERER") or "users:notifications")  # type: ignore[return-value]
