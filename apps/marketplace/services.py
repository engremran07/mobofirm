"""Marketplace logic: seller onboarding, verification and listing moderation."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from .models import Listing, SellerProfile, SellerVerification


class MarketplaceError(ValueError):
    """Raised for invalid marketplace state transitions."""


def public_listings() -> QuerySet[Listing]:
    return Listing.objects.filter(
        status=Listing.Status.APPROVED,
        is_active=True,
        seller__is_active=True,
        seller__verified=True,
    ).select_related("seller", "firmware")


@transaction.atomic
def request_verification(seller: SellerProfile, evidence: str) -> SellerVerification:
    if seller.verified:
        raise MarketplaceError("This seller is already verified.")
    evidence = evidence.strip()
    if not evidence:
        raise MarketplaceError("Evidence is required.")
    if seller.verifications.filter(status=SellerVerification.Status.PENDING).exists():
        raise MarketplaceError("A verification request is already pending.")
    return SellerVerification.objects.create(seller=seller, evidence=evidence)


@transaction.atomic
def review_verification(
    verification_id: int, *, reviewer: Any, approve: bool, reason: str = ""
) -> SellerVerification:
    verification = SellerVerification.objects.select_for_update().get(
        pk=verification_id
    )
    if verification.status != SellerVerification.Status.PENDING:
        raise MarketplaceError("This verification was already reviewed.")
    verification.status = (
        SellerVerification.Status.APPROVED
        if approve
        else SellerVerification.Status.REJECTED
    )
    verification.reviewer = reviewer
    verification.review_reason = reason[:255]
    verification.reviewed_at = timezone.now()
    verification.save()
    if approve:
        SellerProfile.objects.filter(pk=verification.seller_id).update(verified=True)
    return verification


@transaction.atomic
def review_listing(
    listing_id: int, *, reviewer: Any, approve: bool, reason: str = ""
) -> Listing:
    listing = Listing.objects.select_for_update().get(pk=listing_id)
    if listing.status != Listing.Status.PENDING:
        raise MarketplaceError("This listing was already reviewed.")
    listing.status = Listing.Status.APPROVED if approve else Listing.Status.REJECTED
    listing.reviewed_by = reviewer
    listing.reviewed_at = timezone.now()
    listing.review_reason = reason[:255]
    listing.save()
    return listing


def record_view(listing_id: int) -> None:
    Listing.objects.filter(pk=listing_id).update(view_count=F("view_count") + 1)


def seller_summary(seller: SellerProfile) -> dict[str, int]:
    listings = seller.listings.all()
    return {
        "listings": listings.count(),
        "approved": listings.filter(status=Listing.Status.APPROVED).count(),
        "pending": listings.filter(status=Listing.Status.PENDING).count(),
        "views": sum(listings.values_list("view_count", flat=True)),
    }
