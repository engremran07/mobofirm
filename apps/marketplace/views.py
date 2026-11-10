from __future__ import annotations

from typing import Final

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers

from apps.core.decorators import require_app_enabled
from apps.core.search import list_search_context, search_queryset
from apps.firmwares.catalog import get_public_firmware_queryset
from apps.firmwares.models import OfficialFirmware
from apps.pages.widgets import published_page_slugs
from apps.users.request_user import current_user
from apps.users.services.profile_completion import (
    is_profile_complete,
    missing_profile_fields,
    profile_completion_url,
)
from apps.users.services.rate_limit import allow_action

from . import earnings, pickers, services, submissions
from .forms import (
    ListingForm,
    PayoutRequestForm,
    SellerApplicationForm,
    SocialLinkForm,
    SocialLinkFormSet,
    application_data,
)
from .models import MAX_SOCIAL_LINKS, Listing, SellerProfile
from .search_providers import LISTING_FIELDS
from .validators import validate_https_url

SORTS = {
    "newest": "-created_at",
    "price": "price",
    "-price": "-price",
    "popular": "-view_count",
}
SEARCH_LIMIT = 60
VIEWED_SESSION_KEY = "marketplace_viewed_listings"
APPLY_ATTEMPTS: Final[int] = 8
APPLY_WINDOW_SECONDS: Final[int] = 3600
PAYOUT_ATTEMPTS: Final[int] = 10
PAYOUT_WINDOW_SECONDS: Final[int] = 3600
SOCIAL_PREFIX: Final[str] = "links"
MISSING_FIELD_LABELS: Final[dict[str, str]] = {
    "username": "a username that is not your email address",
    "full_name": "your full name",
    "country": "your country",
    "interests": "at least one interest",
    "profile_completed": "confirmation that your profile is complete",
}
TERMS_PAGES: Final[tuple[tuple[str, str], ...]] = (
    ("seller-terms", "Seller terms"),
    ("terms", "Terms of service"),
    ("privacy", "Privacy policy"),
)


def _state_note(listing: Listing) -> str:
    """Why a listing is visible only to its owner (empty when it is public)."""
    if listing.is_public:
        return ""
    if listing.status == Listing.Status.PENDING:
        return "This listing is waiting for staff review and is only visible to you."
    if listing.status == Listing.Status.REJECTED:
        reason = f" Reason: {listing.review_reason}." if listing.review_reason else ""
        return f"Staff did not approve this listing.{reason} It is only visible to you."
    if not listing.is_active:
        return "This listing is hidden. Show it again from your seller dashboard."
    return "Your seller profile is not approved yet, so this listing is not public."


def _terms_links() -> list[tuple[str, str]]:
    live = published_page_slugs()
    return [
        (label, reverse("pages:page", kwargs={"slug": slug}))
        for slug, label in TERMS_PAGES
        if slug in live
    ]


def _safe_social_links(seller: SellerProfile) -> list[tuple[str, str]]:
    """Social links that still pass https validation (defence in depth)."""
    links: list[tuple[str, str]] = []
    for link in seller.social_links.all():
        try:
            validate_https_url(link.url)
        except ValidationError:
            continue
        links.append((link.get_platform_display(), link.url))
    return links


def _safe_website(seller: SellerProfile) -> str:
    if not seller.website:
        return ""
    try:
        return validate_https_url(seller.website)
    except ValidationError:
        return ""


@require_GET
@require_app_enabled("marketplace")
@vary_on_headers("HX-Request")
def listing_browse(request: HttpRequest) -> HttpResponse:
    """Approved listings; ``q`` goes through the shared engine, HTMX gets the grid."""
    sort = request.GET.get("sort", "newest")
    listings = services.public_listings()
    if request.GET.get("featured") == "1":
        listings = listings.filter(is_featured=True)
    result = search_queryset(listings, request.GET.get("q"), LISTING_FIELDS)
    found = list(
        result.queryset.order_by(SORTS.get(sort, SORTS["newest"]))[:SEARCH_LIMIT]
    )
    template = (
        "marketplace/fragments/listing_grid.html"
        if request.headers.get("HX-Request")
        else "marketplace/listing_browse.html"
    )
    return render(
        request,
        template,
        {
            "listings": found,
            "current_sort": sort,
            **list_search_context(
                result, len(found), user=request.user, kinds=("brand", "model", "tool")
            ),
        },
    )


@require_GET
@require_app_enabled("marketplace")
def listing_detail(request: HttpRequest, pk: int) -> HttpResponse:
    listing = get_object_or_404(
        Listing.objects.select_related("seller", "seller__user", "firmware"), pk=pk
    )
    own = request.user.is_authenticated and listing.seller.user_id == request.user.pk
    if not (listing.is_public or own):
        raise Http404
    viewed = request.session.get(VIEWED_SESSION_KEY, [])
    if listing.is_public and not own and listing.pk not in viewed:
        services.record_view(listing.pk)
        request.session[VIEWED_SESSION_KEY] = [*viewed, listing.pk][-100:]
    related = (
        services.public_listings().filter(seller=listing.seller).exclude(pk=pk)[:4]
    )
    firmware = None
    if listing.firmware_id:
        firmware = (
            get_public_firmware_queryset(OfficialFirmware)
            .select_related("brand", "model")
            .filter(pk=listing.firmware_id)
            .first()
        )
    return render(
        request,
        "marketplace/listing_detail.html",
        {
            "listing": listing,
            "related": related,
            "is_owner": own,
            "firmware": firmware,
            "listing_state_note": _state_note(listing),
        },
    )


@require_GET
@require_app_enabled("marketplace")
def seller_profile(request: HttpRequest, pk: int) -> HttpResponse:
    seller = get_object_or_404(
        SellerProfile.objects.prefetch_related("brands", "chipsets", "social_links"),
        pk=pk,
        is_active=True,
        verified=True,
    )
    return render(
        request,
        "marketplace/seller_profile.html",
        {
            "seller": seller,
            "listings": services.public_listings().filter(seller=seller),
            "social_links": _safe_social_links(seller),
            "website": _safe_website(seller),
            "files": submissions.public_files_for(seller.user),
        },
    )


def _application_context(
    request: HttpRequest,
    form: SellerApplicationForm,
    links: BaseFormSet[SocialLinkForm],
    seller: SellerProfile | None,
) -> dict[str, object]:
    return {
        "form": form,
        "links": links,
        "pickers": [
            pickers.picker_context(name, [obj.pk for obj in form.selected(name)])
            for name in ("brands", "chipsets")
        ],
        "terms_links": _terms_links(),
        "status": services.application_status(seller) if seller else None,
        "display_name": current_user(request).get_full_name(),
        "max_links": MAX_SOCIAL_LINKS,
    }


@require_http_methods(["GET", "POST"])
@require_app_enabled("marketplace")
@login_required
def become_seller(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    if not user.is_verified:
        messages.error(request, "Verify your email address before becoming a seller.")
        return redirect("marketplace:listing_browse")
    if not is_profile_complete(user):
        missing = [
            MISSING_FIELD_LABELS.get(name, name)
            for name in missing_profile_fields(user)
        ]
        messages.info(
            request,
            "Finish your profile before applying as a seller. Still needed: "
            + ", ".join(missing)
            + ". We will bring you back to the seller application afterwards.",
        )
        return redirect(profile_completion_url(reverse("marketplace:become_seller")))
    seller = SellerProfile.objects.filter(user=user).first()
    if seller is not None:
        code = services.application_status(seller).code
        if code == "approved":
            return redirect("marketplace:dashboard")
        if code in {"pending", "suspended"}:
            return redirect("marketplace:application_status")

    if request.method == "POST":
        form = SellerApplicationForm(request.POST)
        links = SocialLinkFormSet(request.POST, prefix=SOCIAL_PREFIX)
        context = _application_context(request, form, links, seller)
        if not allow_action(
            f"marketplace-apply:{user.pk}", APPLY_ATTEMPTS, APPLY_WINDOW_SECONDS
        ):
            form.add_error(None, "Too many attempts. Try again later.")
            return render(
                request, "marketplace/become_seller.html", context, status=429
            )
        if form.is_valid() and links.is_valid():
            try:
                services.submit_application(user, application_data(form, links))
            except services.MarketplaceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request, "Application sent. Staff will review it shortly."
                )
            return redirect("marketplace:application_status")
        return render(request, "marketplace/become_seller.html", context)

    initial: dict[str, object] = {}
    link_initial: list[dict[str, str]] = []
    if seller is not None:
        initial = {
            "bio": seller.bio,
            "website": seller.website,
            "contact_preference": seller.contact_preference,
            "brands": list(seller.brands.values_list("pk", flat=True)),
            "chipsets": list(seller.chipsets.values_list("pk", flat=True)),
        }
        link_initial = [
            {"platform": link.platform, "url": link.url}
            for link in seller.social_links.all()
        ]
    form = SellerApplicationForm(initial=initial)
    links = SocialLinkFormSet(prefix=SOCIAL_PREFIX, initial=link_initial)
    return render(
        request,
        "marketplace/become_seller.html",
        _application_context(request, form, links, seller),
    )


@require_GET
@require_app_enabled("marketplace")
@login_required
def application_status(request: HttpRequest) -> HttpResponse:
    seller = SellerProfile.objects.filter(user=current_user(request)).first()
    if seller is None:
        return redirect("marketplace:become_seller")
    return render(
        request,
        "marketplace/application_status.html",
        {
            "seller": seller,
            "status": services.application_status(seller),
            "social_links": _safe_social_links(seller),
            "website": _safe_website(seller),
            "brands": seller.brands.all(),
            "chipsets": seller.chipsets.all(),
        },
    )


@require_GET
@require_app_enabled("marketplace")
@login_required
def picker(request: HttpRequest, kind: str) -> HttpResponse:
    if not pickers.is_picker_kind(kind):
        raise Http404
    selected = pickers.clean_ids(request.GET.getlist(kind))
    context = pickers.picker_context(kind, selected, request.GET.get("q", ""))
    return render(request, "marketplace/fragments/picker_body.html", context)


def _approved_seller_or_redirect(
    request: HttpRequest,
) -> tuple[SellerProfile | None, HttpResponse | None]:
    seller = SellerProfile.objects.filter(user=current_user(request)).first()
    if seller is None:
        return None, redirect("marketplace:become_seller")
    if not (seller.is_active and seller.verified):
        return None, redirect("marketplace:application_status")
    return seller, None


@require_GET
@require_app_enabled("marketplace")
@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    seller, redirection = _approved_seller_or_redirect(request)
    if seller is None:
        return redirection or redirect("marketplace:become_seller")
    user = current_user(request)
    return render(
        request,
        "marketplace/dashboard.html",
        {
            "seller": seller,
            "summary": services.seller_summary(seller),
            "earnings": earnings.earnings_summary(seller),
            "payout_form": PayoutRequestForm(),
            "submission_summary": submissions.submission_summary(user),
            "submissions": submissions.submissions_for(user),
        },
    )


@require_http_methods(["GET", "POST"])
@require_app_enabled("marketplace")
@login_required
def payout_request(request: HttpRequest) -> HttpResponse:
    """Dialog target and no-JavaScript page for a staff-reviewed payout request."""
    seller, redirection = _approved_seller_or_redirect(request)
    if seller is None:
        return redirection or redirect("marketplace:become_seller")
    summary = earnings.earnings_summary(seller)
    form = PayoutRequestForm(request.POST or None)
    status = 200
    if request.method == "POST":
        if not allow_action(
            f"marketplace-payout:{seller.user_id}",
            PAYOUT_ATTEMPTS,
            PAYOUT_WINDOW_SECONDS,
        ):
            form.add_error(None, "Too many requests. Try again later.")
            status = 429
        elif form.is_valid():
            try:
                earnings.request_payout(
                    seller,
                    form.cleaned_data["credits"],
                    method=form.cleaned_data["method"],
                    destination=form.cleaned_data["destination"],
                )
            except services.MarketplaceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(
                    request, "Payout request sent. Staff will review it shortly."
                )
                return redirect("marketplace:dashboard")
    return render(
        request,
        "marketplace/payout_request.html",
        {"seller": seller, "earnings": summary, "payout_form": form},
        status=status,
    )


@require_http_methods(["GET", "POST"])
@require_app_enabled("marketplace")
@login_required
def my_listings(request: HttpRequest) -> HttpResponse:
    seller, redirection = _approved_seller_or_redirect(request)
    if seller is None:
        return redirection or redirect("marketplace:become_seller")
    form = ListingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        listing = form.save(commit=False)
        listing.seller = seller
        listing.save()
        messages.success(request, "Listing submitted for review.")
        return redirect("marketplace:my_listings")
    return render(
        request,
        "marketplace/my_listings.html",
        {
            "seller": seller,
            "listings": seller.listings.all(),
            "form": form,
            "summary": services.seller_summary(seller),
        },
    )


@require_POST
@require_app_enabled("marketplace")
@login_required
def toggle_listing(request: HttpRequest, pk: int) -> HttpResponse:
    seller = services.seller_for(current_user(request))
    listing = get_object_or_404(Listing, pk=pk, seller=seller) if seller else None
    if listing is None:
        raise Http404
    listing.is_active = not listing.is_active
    listing.save(update_fields=["is_active", "updated_at"])
    return redirect("marketplace:my_listings")
