"""My files: the library of files served to the user, their uploads and their quota."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from apps.core.search import list_search_context
from apps.users.request_user import current_user

from . import library

PAGE_SIZE = 20


def _tabs_context(active: str) -> dict[str, object]:
    return {
        "my_files_tab": active,
        "shop_available": library.shop_available(),
        "firmwares_available": library.firmwares_available(),
    }


@require_GET
@login_required
@vary_on_headers("HX-Request")
def library_view(request: HttpRequest) -> HttpResponse:
    """Everything served to the user (free, paid, featured) with filters."""
    user = current_user(request)
    filters = library.LibraryFilters.from_query(request.GET)
    tab = (
        library.HISTORY if request.GET.get("tab") == library.HISTORY else library.ACTIVE
    )
    everything = library.library_entries(user)
    in_tab = library.entries_for_tab(everything, tab)
    outcome = library.search_library(in_tab, filters)
    page = Paginator(outcome.items, PAGE_SIZE)
    history_count = sum(1 for entry in everything if entry.state == library.HISTORY)
    context = {
        **_tabs_context("library"),
        **list_search_context(outcome, len(outcome.items), kinds=()),
        "filters": filters,
        "library_tab": tab,
        "library_tabs": library.LIBRARY_TABS,
        "active_count": len(everything) - history_count,
        "history_count": history_count,
        "page_obj": page.get_page(request.GET.get("page")),
        "total_served": len(in_tab),
        "brands": library.brand_choices(in_tab),
        "tiers": library.TIERS,
        "quota": library.quota_panel(user),
    }
    template = (
        "users/library/_results.html"
        if request.headers.get("HX-Request")
        else "users/library/library.html"
    )
    return render(request, template, context)


@require_GET
@login_required
@vary_on_headers("HX-Request")
def uploads_view(request: HttpRequest) -> HttpResponse:
    """The user's own firmware submissions and where each one stands."""
    user = current_user(request)
    status = request.GET.get("status", "")
    status = status if status in dict(library.UPLOAD_STATUSES) else ""
    page = Paginator(library.upload_entries(user, status), PAGE_SIZE)
    context = {
        **_tabs_context("uploads"),
        "status": status,
        "statuses": library.UPLOAD_STATUSES,
        "page_obj": page.get_page(request.GET.get("page")),
    }
    template = (
        "users/library/_uploads_results.html"
        if request.headers.get("HX-Request")
        else "users/library/uploads.html"
    )
    return render(request, template, context)


@require_GET
@login_required
def quota_view(request: HttpRequest) -> HttpResponse:
    """Download allowance: limits, usage, reset dates and purchased files to fetch."""
    return render(
        request,
        "users/library/quota.html",
        {**_tabs_context("quota"), "quota": library.quota_panel(current_user(request))},
    )
