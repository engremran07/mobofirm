from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import require_app_enabled
from apps.firmwares.catalog import get_public_firmware_queryset
from apps.firmwares.models import OfficialFirmware
from apps.users.request_user import current_user

from . import services
from .forms import ListingForm, SellerProfileForm, VerificationRequestForm
from .models import Listing, SellerProfile

SORTS = {
    "newest": "-created_at",
    "price": "price",
    "-price": "-price",
    "popular": "-view_count",
}
SEARCH_LIMIT = 60
VIEWED_SESSION_KEY = "marketplace_viewed_listings"


def _seller_or_none(user) -> SellerProfile | None:
    return SellerProfile.objects.filter(user=user, is_active=True).first()


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
    return "Your seller profile is not verified yet, so this listing is not public."


@require_GET
@require_app_enabled("marketplace")
def listing_browse(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip()[:100]
    sort = request.GET.get("sort", "newest")
    listings = services.public_listings()
    if request.GET.get("featured") == "1":
        listings = listings.filter(is_featured=True)
    if query:
        listings = listings.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    listings = listings.order_by(SORTS.get(sort, SORTS["newest"]))[:SEARCH_LIMIT]
    template = (
        "marketplace/fragments/listing_grid.html"
        if request.headers.get("HX-Request")
        else "marketplace/listing_browse.html"
    )
    return render(
        request,
        template,
        {"listings": listings, "search_query": query, "current_sort": sort},
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
    seller = get_object_or_404(SellerProfile, pk=pk, is_active=True, verified=True)
    listings = services.public_listings().filter(seller=seller)
    return render(
        request,
        "marketplace/seller_profile.html",
        {"seller": seller, "listings": listings},
    )


@require_http_methods(["GET", "POST"])
@require_app_enabled("marketplace")
@login_required
def become_seller(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    if SellerProfile.objects.filter(user=user).exists():
        return redirect("marketplace:my_listings")
    if not user.is_verified:
        messages.error(request, "Verify your email address before becoming a seller.")
        return redirect("marketplace:listing_browse")
    form = SellerProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        seller = form.save(commit=False)
        seller.user = request.user
        seller.save()
        messages.success(request, "Seller profile created.")
        return redirect("marketplace:my_listings")
    return render(request, "marketplace/become_seller.html", {"form": form})


@require_http_methods(["GET", "POST"])
@require_app_enabled("marketplace")
@login_required
def my_listings(request: HttpRequest) -> HttpResponse:
    seller = _seller_or_none(request.user)
    if seller is None:
        return redirect("marketplace:become_seller")
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
            "verification_form": VerificationRequestForm(),
            "latest_verification": seller.verifications.first(),
            "pending_verification": seller.verifications.filter(
                status="pending"
            ).exists(),
        },
    )


@require_POST
@require_app_enabled("marketplace")
@login_required
def toggle_listing(request: HttpRequest, pk: int) -> HttpResponse:
    seller = _seller_or_none(request.user)
    listing = get_object_or_404(Listing, pk=pk, seller=seller) if seller else None
    if listing is None:
        raise Http404
    listing.is_active = not listing.is_active
    listing.save(update_fields=["is_active", "updated_at"])
    return redirect("marketplace:my_listings")


@require_POST
@require_app_enabled("marketplace")
@login_required
def request_verification(request: HttpRequest) -> HttpResponse:
    seller = _seller_or_none(request.user)
    if seller is None:
        return redirect("marketplace:become_seller")
    form = VerificationRequestForm(request.POST)
    if form.is_valid():
        try:
            services.request_verification(seller, form.cleaned_data["evidence"])
        except services.MarketplaceError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Verification request submitted.")
    else:
        messages.error(request, "Provide evidence of up to 2000 characters.")
    return redirect("marketplace:my_listings")
