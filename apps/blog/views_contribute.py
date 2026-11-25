"""Member blog contributions: the contribute page, editing and withdrawing.

Every write requires a signed-in member with a verified email, CSRF, and redirects
after success. A contribution only ever enters the review queue; publishing stays with
staff (see :mod:`apps.blog.services.contributions`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.blog import bounty_gateway
from apps.blog.access import blog_enabled_for
from apps.blog.forms import ContributionForm
from apps.blog.models import Post, PostAccess
from apps.blog.services import contribution_title_matches, contributions
from apps.blog.services.contributions import ContributionData, ContributionError
from apps.core.app_service import app_enabled
from apps.core.search.text import clean_query
from apps.users.services.rate_limit import allow_action

logger = logging.getLogger(__name__)

View = Callable[..., HttpResponse]


def verified_member_required(view: View) -> View:
    """Signed in (redirect to login), then email verified (redirect to verify)."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not contributions.is_verified(request.user):
            messages.warning(
                request, "Verify your email address before contributing a post."
            )
            request.session["next_after_verify"] = request.get_full_path()
            return redirect("users:verify_email_required")
        return view(request, *args, **kwargs)

    return login_required(wrapper)


def _closed(request: HttpRequest) -> HttpResponse:
    return render(request, "blog/contribute_closed.html", status=403)


def _hub_url(post: Post | None = None) -> str:
    """Where a successful write lands: the member's contributions, when available."""
    try:
        return reverse("contributions:blog")
    except NoReverseMatch:
        if post is not None:
            return reverse("blog:contribute_edit", kwargs={"slug": post.slug})
        return reverse("blog:contribute")


def _data(form: ContributionForm) -> ContributionData:
    values = form.cleaned_data
    return ContributionData(
        title=values["title"],
        body=values["body"],
        summary=values.get("summary", ""),
        category=values.get("category"),
        tags=values.get("tags", ()),
        access=values.get("access") or PostAccess.FREE,
        proposed_price=values.get("proposed_price"),
    )


def _become_seller_url() -> str:
    """The marketplace seller application, or ``""`` without that optional app."""
    if not app_enabled("marketplace"):
        return ""
    try:
        return reverse("marketplace:become_seller")
    except NoReverseMatch:
        return ""


@verified_member_required
@require_http_methods(["GET", "POST"])
def contribute(request: HttpRequest) -> HttpResponse:
    """Write a new post for review."""
    if not blog_enabled_for(request.user):
        raise Http404("The blog is disabled.")
    if not contributions.is_open_to(request.user):
        return _closed(request)
    if request.method == "POST":
        form = ContributionForm(
            request.POST, user=request.user, check_title_matches=True
        )
        if form.is_valid():
            try:
                result = contributions.submit(request.user, _data(form))
            except ContributionError as error:
                form.add_error(None, str(error))
            else:
                if result.created:
                    messages.success(
                        request,
                        f"“{result.post.title}” was submitted for review. "
                        "You will be notified of the decision.",
                    )
                else:
                    messages.info(
                        request,
                        f"“{result.post.title}” is already waiting for review.",
                    )
                return redirect(_hub_url(result.post))
    else:
        form = ContributionForm(user=request.user, check_title_matches=True)
    return render(
        request,
        "blog/contribute_form.html",
        {
            "form": form,
            "editing_post": None,
            "remaining_today": contributions.remaining_today(request.user),
            "hub_url": _hub_url(),
            "open_bounties": bounty_gateway.open_guide_bounties(),
            "seller": form.seller,
            "become_seller_url": "" if form.seller else _become_seller_url(),
        },
    )


@verified_member_required
@require_GET
@never_cache
def contribute_title_matches(request: HttpRequest) -> HttpResponse:
    """Return bounded public title suggestions without blocking a submission."""
    if not contributions.is_open_to(request.user):
        return render(
            request,
            "blog/partials/contribution_title_matches.html",
            {"matches": [], "error": "Title matching is unavailable for this account."},
            status=403,
        )
    title = clean_query(request.GET.get("title", "")[:200])
    if len(title) < contribution_title_matches.MIN_QUERY_LENGTH:
        return HttpResponse("")
    if not allow_action(
        f"blog:contribution-title-check:{request.user.pk}", 30, window_seconds=60
    ):
        return render(
            request,
            "blog/partials/contribution_title_matches.html",
            {
                "matches": [],
                "error": "Please wait a moment before checking another title.",
            },
            status=429,
        )
    try:
        matches = contribution_title_matches.find_matches(title, user=request.user)
    except DatabaseError:
        logger.exception("Contribution title-match search failed")
        return render(
            request,
            "blog/partials/contribution_title_matches.html",
            {
                "matches": [],
                "error": "Title suggestions are temporarily unavailable. You can still submit for review.",
            },
            status=503,
        )
    return render(
        request,
        "blog/partials/contribution_title_matches.html",
        {"matches": matches, "error": ""},
    )


def _own_post(request: HttpRequest, slug: str) -> Post:
    post = get_object_or_404(Post, slug__iexact=slug)
    if post.author_id != request.user.pk or not post.is_community_contribution:
        raise Http404
    return post


@verified_member_required
@require_http_methods(["GET", "POST"])
def contribute_edit(request: HttpRequest, slug: str) -> HttpResponse:
    """Edit a contribution that is waiting for review or was sent back."""
    post = _own_post(request, slug)
    if not contributions.can_edit(request.user, post):
        messages.error(
            request, "Only a contribution still in review or sent back can be edited."
        )
        return redirect(_hub_url(post))
    if request.method == "POST":
        form = ContributionForm(request.POST, instance=post, user=request.user)
        if form.is_valid():
            try:
                contributions.update(request.user, post, _data(form))
            except ContributionError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, "Your changes were saved.")
                return redirect(_hub_url(post))
    else:
        form = ContributionForm(instance=post, user=request.user)
    return render(
        request,
        "blog/contribute_form.html",
        {
            "form": form,
            "editing_post": post,
            "can_withdraw": contributions.can_withdraw(request.user, post),
            "hub_url": _hub_url(post),
            "seller": form.seller,
            "become_seller_url": "" if form.seller else _become_seller_url(),
        },
    )


@verified_member_required
@require_POST
def contribute_withdraw(request: HttpRequest, slug: str) -> HttpResponse:
    """Take a pending contribution back out of review."""
    post = _own_post(request, slug)
    try:
        contributions.withdraw(request.user, post)
    except (ContributionError, PermissionDenied) as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            f"“{post.title}” was withdrawn. Edit it and resubmit whenever you like.",
        )
    return redirect(_hub_url(post))
