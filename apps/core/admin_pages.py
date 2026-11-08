"""Render staff tool pages inside the Unfold admin shell.

Every staff screen that is not a model admin (SEO centre, ad manager, storage status,
tag manager, ...) renders through ``render_admin_page`` so it always has the sidebar,
header, theme switch and the shared ``mf-ui`` styles from
``frontend/src/styles/admin-ui.css``. Templates extend ``admin/staff_page.html``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.translation import gettext as _


def admin_page_context(
    request: HttpRequest,
    *,
    title: str,
    subtitle: str = "",
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Admin-site context (sidebar, branding) plus the page title for a staff screen."""
    return {
        **admin.site.each_context(request),
        "title": title,
        "mf_subtitle": subtitle,
        "mf_home_label": _("Dashboard"),
        **(context or {}),
    }


def render_admin_page(
    request: HttpRequest,
    template: str,
    *,
    title: str,
    subtitle: str = "",
    context: Mapping[str, Any] | None = None,
    status: int = 200,
) -> HttpResponse:
    return render(
        request,
        template,
        admin_page_context(request, title=title, subtitle=subtitle, context=context),
        status=status,
    )
