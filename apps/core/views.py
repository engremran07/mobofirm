# apps/core/views.py
"""
Core views â€” Enterprise-grade, Django 5.2+ ready.

Hardened to:
 - never return async/coroutine objects
 - never leak errors from ORM calls in async event loops
 - return only serializable objects to templates
 - load only existing templates safely
 - take site identity from apps.site_settings (templates read ``site_settings``)
"""

from __future__ import annotations

import importlib
import logging
import sys
import time
from collections.abc import Iterable
from typing import Any

import django
from django.apps import apps as django_apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connections
from django.db import Error as DBError
from django.db.models import QuerySet
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import render
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse
from django.utils.timezone import now
from django.views.decorators.cache import never_cache

from apps.core.cache import CACHE_BACKEND_ERRORS
from apps.site_settings.services import get_site_settings

logger = logging.getLogger(__name__)

# Valid home templates
_HOME_TEMPLATE_PRIORITY: list[str] = ["core/home.html"]
START_TIME = time.time()


# ============================================================
# INTERNAL UTILITIES
# ============================================================
def _optional_queryset(app_label: str, model_name: str) -> QuerySet[Any] | None:
    """Queryset for a model of an optional app, or None when it is not installed."""
    try:
        model = django_apps.get_model(app_label, model_name)
    except LookupError:
        return None
    return model.objects.all()


def _safe_count(qs: QuerySet[Any] | None) -> int:
    """Count a queryset, returning 0 when it is missing or the database fails."""
    if qs is None:
        return 0
    try:
        return qs.count()
    except DatabaseError as exc:
        logger.debug("_safe_count fallback 0: %s", exc)
        return 0


def _safe_iter(qs: QuerySet[Any] | None, limit: int = 5) -> list[Any]:
    """Newest-first slice of a queryset, empty when missing or the database fails."""
    if qs is None:
        return []
    try:
        return list(qs.order_by("-created_at")[:limit])
    except DatabaseError as exc:
        logger.debug("_safe_iter fallback empty: %s", exc)
        return []


# ============================================================
# SAFE RENDERING WRAPPER
# ============================================================
def _render_safe(
    request: HttpRequest, template: str, context: dict[str, Any], status: int = 200
) -> HttpResponse:
    """Completely safe render wrapper."""
    try:
        return render(request, template, context, status=status)
    except TemplateDoesNotExist as exc:
        logger.warning("Missing template: %s (%s)", template, exc)
        sn = context.get("site_name") or get_site_settings().site_name
        return HttpResponse(
            f"<html><head><title>{sn}</title></head>"
            f"<body><h1>{sn}</h1><p>Content temporarily unavailable.</p></body></html>",
            status=status,
        )
    except (TemplateSyntaxError, NoReverseMatch, DBError, ImproperlyConfigured):
        logger.exception("Render error for %s", template)
        return HttpResponseServerError("Internal server error")


def _first_existing_template(candidates: Iterable[str]) -> str | None:
    """Pick first existing template (safe)."""
    for name in candidates:
        try:
            get_template(name)
        except TemplateDoesNotExist:
            continue
        except TemplateSyntaxError:
            logger.exception("Template %s has a syntax error", name)
            continue
        return name
    return None


# ============================================================
# HOME PAGE
# ============================================================
@never_cache
def home(request: HttpRequest) -> HttpResponse:
    snapshot = get_site_settings()

    # Maintenance mode?
    if snapshot.maintenance_mode:
        return _render_safe(
            request,
            "errors/503.html",
            {
                "site_name": snapshot.site_name,
                "message": snapshot.maintenance_message
                or "This site is currently under maintenance.",
            },
            status=503,
        )

    users_qs = _optional_queryset("users", "CustomUser")
    notifications_qs = _optional_queryset("users", "Notification")
    if notifications_qs is not None:
        notifications_qs = (
            notifications_qs.filter(recipient=request.user, is_read=False)
            if request.user.is_authenticated
            else notifications_qs.none()
        )
    announcements_qs = _optional_queryset("users", "Announcement")
    if announcements_qs is not None:
        announcements_qs = announcements_qs.filter(is_active=True)

    context = {
        "site_name": snapshot.site_name,
        "django_version": django.get_version(),
        "python_version": sys.version.split()[0],
        "now": now(),
        "total_users": _safe_count(users_qs),
        "unread_notifications": _safe_count(notifications_qs),
        "active_announcements": _safe_count(announcements_qs),
        "announcements": _safe_iter(announcements_qs, limit=5),
    }

    # Select homepage template
    template_name = _first_existing_template(_HOME_TEMPLATE_PRIORITY)
    if template_name:
        return _render_safe(request, template_name, context)

    # Ultimate fallback
    logger.error("No homepage template found among: %s", _HOME_TEMPLATE_PRIORITY)
    sn = context["site_name"]
    return HttpResponse(
        f"<html><head><title>{sn}</title></head>"
        f"<body><h1>{sn}</h1><p>Home page temporarily unavailable.</p></body></html>",
        status=503,
    )


def health_check(request: HttpRequest) -> JsonResponse:
    """
    Enterprise-grade health probe with lightweight dependency checks.
    """
    checks: dict[str, dict[str, Any]] = {}
    optional: dict[str, dict[str, Any]] = {}
    meta = {
        "uptime_seconds": int(time.time() - START_TIME),
        "django_version": django.get_version(),
        "python_version": sys.version.split()[0] if sys.version else "",
        "debug": bool(getattr(settings, "DEBUG", False)),
    }

    # DB check (default connection)
    try:
        conn = connections["default"]
        start = time.perf_counter()
        conn.ensure_connection()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        checks["db"] = {"ok": True, "latency_ms": latency_ms}
    except DBError as exc:
        logger.warning("Health check DB failed: %s", exc)
        checks["db"] = {"ok": False, "error": str(exc)}

    # Cache check (set/get round-trip)
    try:
        cache_key = "healthcheck_ping"
        cache.set(cache_key, "pong", timeout=5)
        ok = cache.get(cache_key) == "pong"
        checks["cache"] = {"ok": ok}
    except CACHE_BACKEND_ERRORS as exc:
        logger.warning("Health check cache failed: %s", exc)
        checks["cache"] = {"ok": False, "error": str(exc)}

    # Optional: Celery broker reachability (if configured)
    broker_url = getattr(settings, "CELERY_BROKER_URL", None) or getattr(
        settings, "BROKER_URL", None
    )
    if broker_url:
        try:
            kombu = importlib.import_module("kombu")
            kombu_exceptions = importlib.import_module("kombu.exceptions")
        except ModuleNotFoundError:
            optional["broker"] = {"ok": True, "skipped": "kombu_not_installed"}
        else:
            start = time.perf_counter()
            try:
                with kombu.Connection(broker_url, connect_timeout=1) as broker_conn:
                    broker_conn.ensure_connection(max_retries=1)
            except (OSError, kombu_exceptions.KombuError) as exc:
                logger.warning("Health check broker failed: %s", exc)
                optional["broker"] = {"ok": False, "error": str(exc)}
            else:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                optional["broker"] = {"ok": True, "latency_ms": latency_ms}

    overall_ok = all(v.get("ok") for v in checks.values()) if checks else True
    status = 200 if overall_ok else 503
    return JsonResponse(
        {
            "ok": overall_ok,
            "status": "healthy" if overall_ok else "degraded",
            "checks": checks,
            "optional": optional,
            "meta": meta,
        },
        status=status,
    )


# ============================================================
# ERROR HANDLERS
# ============================================================
def staff_console_template(request: HttpRequest, public: str, staff: str) -> str:
    """``staff`` for requests inside the staff console, ``public`` everywhere else."""
    try:
        prefix = reverse("admin:index")
    except NoReverseMatch:
        return public
    return staff if request.path_info.startswith(prefix) else public


def error_400_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        "errors/400.html",
        {"error": str(exception or "")},
        status=400,
    )


def error_403_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        staff_console_template(request, "errors/403.html", "admin/403.html"),
        {"error": str(exception or "")},
        status=403,
    )


def error_404_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        staff_console_template(request, "errors/404.html", "admin/404.html"),
        {"error": str(exception or "")},
        status=404,
    )


def error_500_view(request: HttpRequest) -> HttpResponse:
    return _render_safe(
        request,
        staff_console_template(request, "errors/500.html", "admin/500.html"),
        {},
        status=500,
    )
