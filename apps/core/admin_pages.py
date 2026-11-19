"""Render custom staff pages inside the staff console shell.

Every staff screen that is not a ``ModelAdmin`` view (SEO centre, ad manager, storage
status, tag manager, distribution, ...) renders through ``render_admin_page`` (or builds
its context with ``admin_page_context``) so it gets the console shell: navigation,
branding, theme and messages. Templates extend ``admin/staff_page.html`` and compose the
``{% staff_* %}`` tags from ``apps.core.templatetags.staff_ui``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

Breadcrumb = tuple[str, str | None]


def admin_page_context(
    request: HttpRequest,
    *,
    title: str,
    subtitle: str = "",
    breadcrumbs: Sequence[Breadcrumb] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Admin-site context (navigation, branding) plus the header of a staff screen.

    ``breadcrumbs`` is a list of ``(label, url)``; the last crumb is the current page
    (its url is ignored). Without it the header shows Dashboard > ``title``. The
    subtitle is exposed as ``page_subtitle``.
    """
    return {
        **admin.site.each_context(request),
        "title": title,
        "page_subtitle": subtitle,
        "page_breadcrumbs": _crumbs(breadcrumbs),
        **(context or {}),
    }


def render_admin_page(
    request: HttpRequest,
    template: str,
    *,
    title: str,
    subtitle: str = "",
    breadcrumbs: Sequence[Breadcrumb] | None = None,
    context: Mapping[str, Any] | None = None,
    status: int = 200,
) -> HttpResponse:
    """Render ``template`` as a staff page with the console shell context."""
    return render(
        request,
        template,
        admin_page_context(
            request,
            title=title,
            subtitle=subtitle,
            breadcrumbs=breadcrumbs,
            context=context,
        ),
        status=status,
    )


def _crumbs(breadcrumbs: Sequence[Breadcrumb] | None) -> list[Breadcrumb]:
    if not breadcrumbs:
        return []
    *parents, (current, _url) = breadcrumbs
    return [*parents, (current, None)]
