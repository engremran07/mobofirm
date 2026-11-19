"""Shell data and global search of the staff console.

``shell_context`` is what the console layout needs on every page (branding,
navigation, command-palette entries); the layout's template tags call it lazily so a
page renders correctly even when the view did not pass ``each_context`` data.
``console_search`` powers the header search box: it matches navigation entries and
the records of a few core models, always limited to what the user may view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.utils import quote
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, URLPattern, path, reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from apps.core import admin_branding
from apps.core.admin_navigation import account_links, build_sidebar, palette_items

logger = logging.getLogger(__name__)

SEARCH_MODELS: tuple[str, ...] = (
    "users.customuser",
    "firmwares.brand",
    "firmwares.model",
    "firmwares.variant",
    "firmwares.chipset",
    "firmwares.pendingfirmware",
    "firmwares.officialfirmware",
    "blog.post",
    "tags.tag",
    "comments.comment",
    "pages.page",
    "shop.order",
    "marketplace.listing",
)
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
RECORDS_PER_MODEL = 4
NAVIGATION_RESULTS = 8


def brand_context(request: HttpRequest) -> dict[str, Any]:
    """Branding shown in the sidebar, header and footer."""
    label, variant = admin_branding.environment_label(request)
    return {
        "title": admin_branding.site_title(request),
        "header": admin_branding.site_header(request),
        "subheader": admin_branding.site_subheader(request),
        "version": admin_branding.site_version(request),
        "site_url": admin_branding.site_url(request),
        "health_url": admin_branding.health_url(request),
        "logo": admin_branding.site_icon(request),
        "favicons": admin_branding.favicons(request),
        "environment": {"label": label, "variant": variant},
    }


def shell_context(request: HttpRequest) -> dict[str, Any]:
    """Branding, navigation and palette data for one request."""
    sidebar = build_sidebar(request)
    return {
        "console_brand": brand_context(request),
        "sidebar_navigation": sidebar,
        "console_palette_items": palette_items(sidebar),
        "console_account_links": account_links(request),
    }


@dataclass(frozen=True, slots=True)
class RecordHit:
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class RecordGroup:
    title: str
    icon: str
    hits: list[RecordHit]
    changelist_url: str


def _navigation_hits(request: HttpRequest, query: str) -> list[dict[str, str]]:
    needle = query.casefold()
    hits = [
        item
        for item in palette_items(build_sidebar(request))
        if needle in item["title"].casefold() or needle in item["group"].casefold()
    ]
    return hits[:NAVIGATION_RESULTS]


def _record_groups(request: HttpRequest, query: str) -> list[RecordGroup]:
    groups: list[RecordGroup] = []
    for label in SEARCH_MODELS:
        try:
            model = django_apps.get_model(label)
        except LookupError:
            continue
        if not admin.site.is_registered(model):
            continue
        model_admin = admin.site.get_model_admin(model)
        if not model_admin.search_fields or not model_admin.has_view_permission(
            request
        ):
            continue
        meta = model._meta
        try:
            list_url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
        except NoReverseMatch:
            continue
        queryset, may_duplicate = model_admin.get_search_results(
            request, model_admin.get_queryset(request), query
        )
        if may_duplicate:
            queryset = queryset.distinct()
        try:
            found = list(queryset[:RECORDS_PER_MODEL])
        except DatabaseError:
            # One unreadable table must not take the whole palette down.
            logger.warning("Console search skipped %s", meta.label, exc_info=True)
            continue
        hits = []
        for obj in found:
            try:
                url = reverse(
                    f"admin:{meta.app_label}_{meta.model_name}_change",
                    args=[quote(obj.pk)],
                )
            except NoReverseMatch:
                continue
            hits.append(RecordHit(str(obj), url))
        if hits:
            groups.append(
                RecordGroup(
                    str(meta.verbose_name_plural).capitalize(),
                    "search",
                    hits,
                    f"{list_url}?{urlencode({'q': query})}",
                )
            )
    return groups


@require_GET
def console_search(request: HttpRequest) -> HttpResponse:
    """Search navigation and records; a fragment for HTMX, a full page otherwise."""
    query = " ".join(request.GET.get("q", "").split())[:MAX_QUERY_LENGTH]
    searched = len(query) >= MIN_QUERY_LENGTH
    context: dict[str, Any] = {
        "title": _("Search"),
        "query": query,
        "searched": searched,
        "too_short": bool(query) and not searched,
        "navigation_hits": _navigation_hits(request, query) if searched else [],
        "record_groups": _record_groups(request, query) if searched else [],
    }
    is_fragment = request.headers.get("HX-Request") == "true"
    template = (
        "console/search_fragment.html" if is_fragment else "admin/console_search.html"
    )
    return render(request, template, context)


def shell_urls(admin_site: Any) -> list[URLPattern]:
    """URL patterns to prepend to the admin site's own (see ``MobofirmAdminSite``)."""
    return [
        path(
            "console/search/",
            admin_site.admin_view(console_search),
            name="console_search",
        )
    ]
