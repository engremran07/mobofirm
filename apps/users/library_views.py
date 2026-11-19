"""My contributions: the hub, firmware and blog contributions, downloads and quota.

``/contributions/`` is the member's contribution hub. Its tabs show what the member
submitted (firmware and blog posts with review status), the files served to them (the
downloads library) and their download quota. The legacy ``/files/`` addresses redirect
here permanently, keeping the query string.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from apps.core.search import list_search_context
from apps.users.request_user import current_user

from . import library

PAGE_SIZE = 20


def _tabs_context(active: str) -> dict[str, object]:
    return {
        "contributions_tab": active,
        "shop_available": library.shop_available(),
        "firmwares_available": library.firmwares_available(),
        "blog_available": library.blog_available(),
    }


def _permanent(request: HttpRequest, name: str) -> HttpResponsePermanentRedirect:
    """301 to ``name`` keeping the query string (legacy My files addresses)."""
    target = reverse(name)
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{target}?{query}" if query else target)


@require_GET
def legacy_my_files(request: HttpRequest) -> HttpResponse:
    """``/files/`` -> the hub; library filters (``?q=``, ``?tab=``) -> downloads."""
    if request.META.get("QUERY_STRING"):
        return _permanent(request, "contributions:downloads")
    return _permanent(request, "contributions:hub")


@require_GET
def legacy_my_uploads(request: HttpRequest) -> HttpResponse:
    """``/files/uploads/`` -> firmware contributions."""
    return _permanent(request, "contributions:firmware")


@require_GET
def legacy_my_quota(request: HttpRequest) -> HttpResponse:
    """``/files/quota/`` -> the quota tab."""
    return _permanent(request, "contributions:quota")


def _firmware_contribute_url() -> str:
    if not library.firmwares_available():
        return ""
    try:
        return reverse("firmwares:contribute")
    except NoReverseMatch:
        return ""


@require_GET
@login_required
def hub_view(request: HttpRequest) -> HttpResponse:
    """Counts and status chips for everything the member contributed."""
    user = current_user(request)
    uploads = library.upload_entries(user)
    posts = library.blog_contributions(user)
    served = library.library_entries(user)
    history = sum(1 for entry in served if entry.state == library.HISTORY)
    context = {
        **_tabs_context("hub"),
        "firmware_total": len(uploads),
        "firmware_chips": library.status_counts(uploads),
        "blog_total": len(posts),
        "blog_chips": library.status_counts(posts),
        "blog_contribute_url": library.blog_contribute_url(user),
        "firmware_contribute_url": _firmware_contribute_url(),
        "active_downloads": len(served) - history,
        "history_downloads": history,
        "reward_credits": library.reward_credits(user),
    }
    return render(request, "users/library/hub.html", context)


@require_GET
@login_required
def blog_view(request: HttpRequest) -> HttpResponse:
    """Blog posts the member submitted, with review status and the reviewer's note."""
    user = current_user(request)
    page = Paginator(library.blog_contributions(user), PAGE_SIZE)
    context = {
        **_tabs_context("blog"),
        "page_obj": page.get_page(request.GET.get("page")),
        "blog_contribute_url": library.blog_contribute_url(user),
    }
    return render(request, "users/library/blog.html", context)


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
        **_tabs_context("downloads"),
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
        **_tabs_context("firmware"),
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
