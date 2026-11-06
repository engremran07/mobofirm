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
import json
import logging
import sys
import time
from collections.abc import Iterable
from typing import Any

import django
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
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
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core import ai_client
from apps.core.cache import CACHE_BACKEND_ERRORS
from apps.site_settings.services import get_site_settings

logger = logging.getLogger(__name__)

# Valid home templates
_HOME_TEMPLATE_PRIORITY: list[str] = ["core/home.html"]
MAX_QUESTION_CHARS = 4_000
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


def _safe_url(name: str, **kwargs: Any) -> str | None:
    """Best-effort reverse; returns None on failure instead of raising."""
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        logger.debug("reverse failed for %s", name, exc_info=True)
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


# ============================================================
# LEGAL PAGES
# ============================================================
def site_map(request: HttpRequest) -> HttpResponse:
    """
    Render a human-friendly site tree with key navigation links.
    """
    sections: list[dict[str, Any]] = [
        {
            "title": "General",
            "links": [
                {"label": "Home", "url": _safe_url("core:home")},
                {"label": "Blog", "url": _safe_url("blog:post_list")},
                {"label": "Tags", "url": _safe_url("tags:list")},
                {
                    "label": "Privacy Policy",
                    "url": _safe_url("pages:page", kwargs={"slug": "privacy"}),
                },
                {
                    "label": "Terms of Service",
                    "url": _safe_url("pages:page", kwargs={"slug": "terms"}),
                },
                {
                    "label": "Cookies Policy",
                    "url": _safe_url("pages:page", kwargs={"slug": "cookies"}),
                },
                {
                    "label": "Cookie / Consent Settings",
                    "url": _safe_url("consent:privacy_center"),
                },
            ],
        },
        {
            "title": "Account",
            "links": [
                {"label": "Dashboard", "url": _safe_url("users:dashboard")},
                {"label": "Profile", "url": _safe_url("users:profile")},
                {"label": "Notifications", "url": _safe_url("users:notifications")},
                {"label": "Sign in", "url": _safe_url("users:account_login")},
                {"label": "Sign up", "url": _safe_url("users:account_signup")},
            ],
        },
        {
            "title": "Help & Support",
            "links": [
                {"label": "Contact / Health", "url": _safe_url("health_check")},
                {"label": "RSS Feed", "url": _safe_url("blog:feed_rss")},
                {"label": "JSON Feed", "url": _safe_url("blog:feed_json")},
            ],
        },
    ]

    # Drop any links that failed to resolve
    for section in sections:
        section["links"] = [l for l in section["links"] if l.get("url")]

    context = {"site_name": get_site_settings().site_name, "sections": sections}
    return _render_safe(request, "core/site_map.html", context)


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
        "errors/403.html",
        {"error": str(exception or "")},
        status=403,
    )


def error_404_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        "errors/404.html",
        {"error": str(exception or "")},
        status=404,
    )


def error_500_view(request: HttpRequest) -> HttpResponse:
    return _render_safe(
        request,
        "errors/500.html",
        {},
        status=500,
    )


# ============================================================
# AI ASSISTANT ENDPOINT (Frontend widget)
# ============================================================
def _parse_json_body(request: HttpRequest, max_bytes: int = 64_000) -> dict[str, Any]:
    """Safe JSON body parser with size guard."""
    raw = request.body or b""
    if len(raw) > max_bytes:
        return {"__error__": "payload_too_large"}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8", errors="ignore"))
    except (ValueError, RecursionError):
        return {"__error__": "bad_json"}
    if not isinstance(parsed, dict):
        return {"__error__": "bad_json"}
    return parsed


def _enforce_ai_rate_limit(request: HttpRequest) -> JsonResponse | None:
    """
    Optional per-view rate-limit hook. Integrate with middleware flags if present.
    Return a JsonResponse to short-circuit, or None to continue.
    """
    if getattr(request, "ai_rate_limited", False):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)

    # Simple per-session throttle: enforce a short cooldown between requests
    session = getattr(request, "session", None)
    if session is not None:
        if not session.session_key:
            session.save()
        now_ts = timezone.now().timestamp()
        try:
            last = float(session.get("ai_last_ts") or 0)
        except (TypeError, ValueError):
            last = 0.0
        if last and (now_ts - last) < 3:  # 3-second cooldown
            return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
        session["ai_last_ts"] = now_ts
        session.modified = True

    # Per-user soft cap: 10 requests per minute
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        key = f"ai_rl_user_{user.pk}"
        try:
            count = int(cache.get(key, 0) or 0)
            if count >= 10:
                return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
            cache.set(key, count + 1, timeout=60)
        except CACHE_BACKEND_ERRORS as exc:
            # Fail open if cache misbehaves
            logger.warning("AI rate-limit cache unavailable: %s", exc)
    return None


@login_required
@require_POST
def ai_assistant_view(request: HttpRequest) -> JsonResponse:
    """
    Hardened AI assistant endpoint used by the frontend chat widget.

    Expects JSON: {"question": "<user question>"} or {"action": "generate_title", "payload": {...}}
    Returns: {"ok": true, "answer": "<assistant answer>"} or {"ok": false, "error": "..."}
    """
    payload = _parse_json_body(request)
    if payload.get("__error__"):
        return JsonResponse({"ok": False, "error": payload["__error__"]}, status=400)

    question = (payload.get("question") or "").strip()
    action = (payload.get("action") or "").strip()
    if not question and not action:
        return JsonResponse({"ok": False, "error": "empty_question"}, status=400)
    if question and len(question) > MAX_QUESTION_CHARS:
        return JsonResponse({"ok": False, "error": "question_too_long"}, status=400)

    rl_response = _enforce_ai_rate_limit(request)
    if rl_response is not None:
        return rl_response

    try:
        answer: str | dict[str, Any]
        if action:
            raw_inner = payload.get("payload")
            payload_text = (
                str(raw_inner.get("text", "")) if isinstance(raw_inner, dict) else ""
            )
            if action == "generate_title":
                answer = ai_client.generate_title(
                    payload_text or question, request.user
                )
            elif action == "generate_excerpt":
                answer = ai_client.generate_excerpt(
                    payload_text or question, request.user
                )
            elif action == "generate_seo":
                answer = ai_client.generate_seo_description(
                    payload_text or question, request.user
                )
            elif action == "suggest_tags":
                answer = ", ".join(
                    ai_client.suggest_tags(payload_text or question, request.user)
                )
            elif action in ("summarize", "summarize_comments"):
                answer = ai_client.summarize_text(
                    payload_text or question, request.user
                )
            elif action == "moderate":
                answer = ai_client.moderate_text(payload_text or question, request.user)
            else:
                answer = f"AI action '{action}' processed."
        else:
            answer = ai_client.generate_answer(question=question, user=request.user)
    except ai_client.AiClientError:
        logger.exception("ai_assistant_view failed")
        return JsonResponse(
            {
                "ok": False,
                "error": "ai_failure",
                "message": "Assistant is temporarily unavailable.",
            },
            status=503,
        )

    return JsonResponse({"ok": True, "answer": answer}, status=200)
