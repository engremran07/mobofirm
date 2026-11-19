"""Branding of the staff console, resolved per request.

``console_branding(request)`` is what ``MobofirmAdminSite.each_context`` publishes as
``console_branding``; the individual callables stay public for reuse and tests.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import gettext as _

from app.version import build_info, environment_name
from apps.site_settings.services import get_site_settings


def console_branding(request: HttpRequest) -> dict[str, Any]:
    """Everything the shell needs to brand itself (one cached settings read)."""
    label, tone = environment_label(request)
    return {
        "site_title": site_title(request),
        "site_header": site_header(request),
        "site_subheader": site_subheader(request),
        "site_version": site_version(request),
        "site_url": site_url(request),
        "health_url": health_url(request),
        "icon": site_icon(request),
        "favicons": favicons(request),
        "environment": {"label": label, "tone": tone},
    }


def site_title(request: HttpRequest) -> str:
    return _("%(name)s operations") % {"name": get_site_settings().site_header}


def site_header(request: HttpRequest) -> str:
    return get_site_settings().site_header


def site_subheader(request: HttpRequest) -> str:
    return _("Operations console")


def site_version(request: HttpRequest) -> str:
    """Release version (and build SHA when known), from ``app.version``."""
    info = build_info()
    return f"v{info.version}" + (f" · {info.git_sha}" if info.git_sha else "")


def site_url(request: HttpRequest) -> str:
    return reverse("home")


def health_url(request: HttpRequest) -> str:
    return reverse("health-ready")


def site_icon(request: HttpRequest) -> dict[str, str] | None:
    """Light/dark logo for the sidebar; None keeps the configured symbol icon."""
    snapshot = get_site_settings()
    if not snapshot.logo_url:
        return None
    return {"light": snapshot.logo_url, "dark": snapshot.dark_logo_url}


def favicons(request: HttpRequest) -> list[dict[str, str]]:
    url = get_site_settings().favicon_url
    return [{"rel": "icon", "href": url}] if url else []


def environment_label(request: HttpRequest) -> tuple[str, str]:
    """Environment badge shown in the admin header and on the dashboard."""
    if settings.DEBUG:
        return (_("Development"), "warning")
    name = environment_name()
    if name and name != "production":
        return (name.title(), "info")
    return (_("Production"), "success")
