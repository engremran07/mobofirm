# apps/core/views.py
"""
Core views â€” Enterprise-grade, Django 5.2+ ready.

Hardened to:
 - never return async/coroutine objects
 - never leak errors from ORM calls in async event loops
 - return only serializable objects to templates
 - load only existing templates safely
 - provide fully brand-neutral, tenant-safe site settings snapshot
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterable
from typing import Any

import django
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connections
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import render
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

# Snapshot cache keys
_CORE_SETTINGS_SNAPSHOT_KEY = "core_settings_snapshot_v1"
_CORE_SETTINGS_VERSION_KEY = "core_settings_version"

# Valid home templates
_HOME_TEMPLATE_PRIORITY: list[str] = ["core/home.html"]
MAX_QUESTION_CHARS = 4_000
START_TIME = time.time()


# ============================================================
# INTERNAL UTILITIES
# ============================================================
def _safe_count(q: Any) -> int:
    """Safe count for any queryset or iterable."""
    try:
        qs = q() if callable(q) else q
        if qs is None:
            return 0
        if hasattr(qs, "count"):
            return int(qs.count())  # type: ignore[union-attr]
        return len(qs)  # type: ignore[arg-type]
    except Exception as exc:
        logger.debug("_safe_count fallback 0: %s", exc)
        return 0


def _safe_iter(q: Any, limit: int = 5) -> list:
    """Safe slice/iteration over queryset or iterable."""
    try:
        qs = q() if callable(q) else q
        if qs is None:
            return []
        if hasattr(qs, "order_by"):
            return list(qs.order_by("-created_at")[:limit])  # type: ignore[union-attr]
        return list(qs)[:limit]  # type: ignore[arg-type]
    except Exception as exc:
        logger.debug("_safe_iter fallback empty: %s", exc)
        return []


# ============================================================
# SNAPSHOT OF SITE SETTINGS (BRAND-NEUTRAL)
# ============================================================
def _get_site_settings_snapshot() -> dict[str, Any]:
    """
    Returns a fully serializable dict for templates.
    Never returns ORM objects and never raises.
    """

    # Versioned cache key
    try:
        version = cache.get(_CORE_SETTINGS_VERSION_KEY) or 0
        key = f"{_CORE_SETTINGS_SNAPSHOT_KEY}_v{version}"
    except Exception:
        key = _CORE_SETTINGS_SNAPSHOT_KEY

    # Load from cache
    try:
        payload = cache.get(key)
        if payload:
            return payload
    except Exception:
        payload = None

    payload = {
        "site_name": getattr(settings, "SITE_NAME", "Site"),
        "site_header": getattr(settings, "SITE_HEADER", "Admin"),
        "site_description": getattr(settings, "SITE_DESCRIPTION", ""),
        "enable_signup": bool(getattr(settings, "ENABLE_SIGNUP", True)),
        "require_mfa": bool(getattr(settings, "REQUIRE_MFA", False)),
        "maintenance_mode": bool(getattr(settings, "MAINTENANCE_MODE", False)),
        "enable_blog": bool(getattr(settings, "BLOG_ENABLED", False)),
        "enable_blog_comments": bool(getattr(settings, "BLOG_COMMENTS_ENABLED", False)),
        "allow_user_blog_posts": bool(
            getattr(settings, "ALLOW_USER_BLOG_POSTS", False)
        ),
        "primary_color": getattr(settings, "PRIMARY_COLOR", "#0d6efd"),
        "secondary_color": getattr(settings, "SECONDARY_COLOR", "#6c757d"),
        "logo": getattr(settings, "SITE_LOGO_URL", None),
        "dark_logo": getattr(settings, "SITE_DARK_LOGO_URL", None),
        "favicon": getattr(settings, "SITE_FAVICON_URL", None),
    }

    try:
        cache.set(key, payload, timeout=300)
    except Exception:  # noqa: S110
        pass

    return payload


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
        sn = (
            context.get("site_name")
            or context.get("site_settings", {}).get("site_name")
            or "Site"
        )
        return HttpResponse(
            f"<html><head><title>{sn}</title></head>"
            f"<body><h1>{sn}</h1><p>Content temporarily unavailable.</p></body></html>",
            status=status,
        )
    except Exception as exc:
        logger.exception("Render error for %s: %s", template, exc)
        return HttpResponseServerError("Internal server error")


def _first_existing_template(candidates: Iterable[str]) -> str | None:
    """Pick first existing template (safe)."""
    for name in candidates:
        try:
            get_template(name)
            return name
        except TemplateDoesNotExist:
            continue
        except Exception:  # noqa: S112
            continue
    return None


def _safe_url(name: str, **kwargs) -> str | None:
    """Best-effort reverse; returns None on failure instead of raising."""
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        return None
    except Exception:
        logger.debug("reverse failed for %s", name, exc_info=True)
        return None


# ============================================================
# HOME PAGE
# ============================================================
@never_cache
def home(request: HttpRequest) -> HttpResponse:
    settings_snapshot = _get_site_settings_snapshot()

    # Maintenance mode?
    if settings_snapshot.get("maintenance_mode"):
        return _render_safe(
            request,
            "errors/503.html",
            {
                "site_settings": settings_snapshot,
                "site_name": settings_snapshot.get("site_name"),
                "message": "This site is currently under maintenance.",
            },
            status=503,
        )

    # Query factories with lazy imports
    def _u():
        try:
            from django.apps import apps

            CustomUser = apps.get_model("users", "CustomUser")
            return CustomUser.objects.all()
        except Exception:
            return []

    def _n():
        try:
            from django.apps import apps

            Notification = apps.get_model("users", "Notification")
            if request.user.is_authenticated:
                return Notification.objects.filter(
                    recipient=request.user, is_read=False
                )
            return Notification.objects.none()
        except Exception:
            return []

    def _a():
        try:
            from django.apps import apps

            Announcement = apps.get_model("users", "Announcement")
            return Announcement.objects.filter(is_active=True)
        except Exception:
            return []

    try:
        pass  # type: ignore
    except Exception:  # noqa: S110
        pass

    # System info
    try:
        django_version = django.get_version()
    except Exception:
        django_version = "unknown"

    try:
        python_version = sys.version.split()[0]
    except Exception:
        python_version = "unknown"

    context = {
        "site_settings": settings_snapshot,
        "site_name": settings_snapshot.get("site_name"),
        "django_version": django_version,
        "python_version": python_version,
        "now": now(),
        "total_users": _safe_count(_u),
        "unread_notifications": _safe_count(_n),
        "active_announcements": _safe_count(_a),
        "announcements": _safe_iter(_a, limit=5),
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
    site_settings = _get_site_settings_snapshot()

    sections = [
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
        section["links"] = [l for l in section["links"] if l.get("url")]  # noqa: E741

    context = {
        "site_settings": site_settings,
        "site_name": site_settings.get("site_name"),
        "sections": sections,
    }
    return _render_safe(request, "core/site_map.html", context)


def health_check(request: HttpRequest) -> JsonResponse:
    """
    Enterprise-grade health probe with lightweight dependency checks.
    """
    checks = {}
    optional = {}
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
    except Exception as exc:
        logger.warning("Health check DB failed: %s", exc)
        checks["db"] = {"ok": False, "error": str(exc)}

    # Cache check (set/get round-trip)
    try:
        cache_key = "healthcheck_ping"
        cache.set(cache_key, "pong", timeout=5)
        ok = cache.get(cache_key) == "pong"
        checks["cache"] = {"ok": ok}
    except Exception as exc:
        logger.warning("Health check cache failed: %s", exc)
        checks["cache"] = {"ok": False, "error": str(exc)}

    # Optional: Celery broker reachability (if configured)
    broker_url = getattr(settings, "CELERY_BROKER_URL", None) or getattr(
        settings, "BROKER_URL", None
    )
    if broker_url:
        try:
            from kombu import Connection  # type: ignore

            start = time.perf_counter()
            with Connection(broker_url, connect_timeout=1) as conn:
                conn.ensure_connection(max_retries=1)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            optional["broker"] = {"ok": True, "latency_ms": latency_ms}
        except ModuleNotFoundError:
            optional["broker"] = {"ok": True, "skipped": "kombu_not_installed"}
        except Exception as exc:  # pragma: no cover - optional path
            logger.warning("Health check broker failed: %s", exc)
            optional["broker"] = {"ok": False, "error": str(exc)}

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
        {"site_settings": _get_site_settings_snapshot(), "error": str(exception or "")},
        status=400,
    )


def error_403_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        "errors/403.html",
        {"site_settings": _get_site_settings_snapshot(), "error": str(exception or "")},
        status=403,
    )


def error_404_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    return _render_safe(
        request,
        "errors/404.html",
        {"site_settings": _get_site_settings_snapshot(), "error": str(exception or "")},
        status=404,
    )


def error_500_view(request: HttpRequest) -> HttpResponse:
    try:
        return _render_safe(
            request,
            "errors/500.html",
            {"site_settings": _get_site_settings_snapshot()},
            status=500,
        )
    except Exception:
        return HttpResponseServerError("Internal server error")


# ============================================================
# AI ASSISTANT ENDPOINT (Frontend widget)
# ============================================================
def _parse_json_body(request: HttpRequest, max_bytes: int = 64_000) -> dict:
    """Safe JSON body parser with size guard."""
    raw = request.body or b""
    if len(raw) > max_bytes:
        return {"__error__": "payload_too_large"}
    if not raw:
        return {}
    try:
        import json

        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return {"__error__": "bad_json"}


def _enforce_ai_rate_limit(request: HttpRequest) -> JsonResponse | None:
    """
    Optional per-view rate-limit hook. Integrate with middleware flags if present.
    Return a JsonResponse to short-circuit, or None to continue.
    """
    try:
        if getattr(request, "ai_rate_limited", False):
            return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)

        # Simple per-session throttle: enforce a short cooldown between requests
        session = getattr(request, "session", None)
        if session is not None:
            if not session.session_key:
                session.save()
            last = session.get("ai_last_ts")
            now_ts = timezone.now().timestamp()
            if last and (now_ts - float(last)) < 3:  # 3-second cooldown
                return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
            session["ai_last_ts"] = now_ts
            session.modified = True

        # Per-user soft cap: 10 requests per minute
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            key = f"ai_rl_user_{user.pk}"
            try:
                count = cache.get(key, 0)
                if count and int(count) >= 10:
                    return JsonResponse(
                        {"ok": False, "error": "rate_limited"}, status=429
                    )
                cache.set(key, int(count) + 1, timeout=60)
            except Exception:  # noqa: S110
                # Fail open if cache misbehaves
                pass
    except Exception:  # noqa: S110
        pass
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
        from apps.core import ai_client  # type: ignore

        if action:
            payload_text = ""
            try:
                payload_text = (payload.get("payload") or {}).get("text", "")
            except Exception:
                payload_text = ""
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
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("ai_assistant_view failed: %s", exc)
        return JsonResponse(
            {
                "ok": False,
                "error": "ai_failure",
                "message": "Assistant is temporarily unavailable.",
            },
            status=503,
        )

    return JsonResponse({"ok": True, "answer": answer}, status=200)
