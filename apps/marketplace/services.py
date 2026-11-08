"""Marketplace logic: seller onboarding, review and listing moderation.

Public contract used by other apps: :func:`is_active_seller` (approved and
active seller only) and :func:`seller_for`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.users.services.notifications import send_notification

from .models import (
    MAX_BIO_LENGTH,
    MAX_SOCIAL_LINKS,
    ContactPreference,
    Listing,
    SellerProfile,
    SellerSocialLink,
    SellerVerification,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AnonymousUser

    from apps.firmwares.models import Brand, Chipset
    from apps.users.models import CustomUser

MAX_REVIEWER_NOTIFICATIONS: Final[int] = 50


class MarketplaceError(ValueError):
    """Raised for invalid marketplace state transitions."""


def seller_for(user: CustomUser | AnonymousUser | None) -> SellerProfile | None:
    """The user's active seller profile in any review state, or ``None``.

    Use :func:`is_active_seller` to gate seller-only behavior.
    """
    if user is None or not user.is_authenticated:
        return None
    return SellerProfile.objects.filter(user=user, is_active=True).first()


def is_active_seller(user: CustomUser | AnonymousUser | None) -> bool:
    """True only for an approved (verified) seller whose profile is active."""
    if user is None or not user.is_authenticated:
        return False
    return SellerProfile.objects.filter(
        user=user, is_active=True, verified=True
    ).exists()


def public_listings() -> QuerySet[Listing]:
    return Listing.objects.filter(
        status=Listing.Status.APPROVED,
        is_active=True,
        seller__is_active=True,
        seller__verified=True,
    ).select_related("seller", "firmware")


@dataclass(frozen=True)
class ApplicationStatus:
    """Where a seller application stands, for the status page and dashboard."""

    code: str
    label: str
    tone: str
    message: str
    reason: str = ""
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None


def application_status(seller: SellerProfile) -> ApplicationStatus:
    latest = seller.verifications.first()
    if not seller.is_active:
        return ApplicationStatus(
            "suspended",
            "Suspended",
            "danger",
            "Staff deactivated this seller profile. Contact staff if you think "
            "this is a mistake.",
        )
    if seller.verified:
        return ApplicationStatus(
            "approved",
            "Approved seller",
            "success",
            "Staff approved your seller profile. Your approved listings are public.",
            reviewed_at=latest.reviewed_at if latest else None,
        )
    if latest is None:
        return ApplicationStatus(
            "not_submitted",
            "Not submitted",
            "",
            "Your application has not been sent to staff yet.",
        )
    if latest.status == SellerVerification.Status.PENDING:
        return ApplicationStatus(
            "pending",
            "Waiting for staff review",
            "warning",
            "Staff are reviewing your application. You will get a notification "
            "when a decision is made.",
            submitted_at=latest.submitted_at,
        )
    return ApplicationStatus(
        "rejected",
        "Not approved",
        "danger",
        "Staff did not approve your application. Update it and send it again.",
        reason=latest.review_reason,
        submitted_at=latest.submitted_at,
        reviewed_at=latest.reviewed_at,
    )


@dataclass(frozen=True)
class SocialLinkData:
    platform: str
    url: str


@dataclass(frozen=True)
class ApplicationData:
    """Validated seller-specific application fields."""

    bio: str
    website: str = ""
    contact_preference: str = ContactPreference.SOCIAL
    brands: list[Brand] = field(default_factory=list)
    chipsets: list[Chipset] = field(default_factory=list)
    social_links: list[SocialLinkData] = field(default_factory=list)
    accepted_terms: bool = False


def _evidence(data: ApplicationData) -> str:
    lines = [data.bio.strip()]
    if data.website:
        lines.append(f"Website: {data.website}")
    lines.extend(f"{link.platform}: {link.url}" for link in data.social_links)
    return "\n".join(lines)[:MAX_BIO_LENGTH]


def _reviewers() -> list[CustomUser]:
    user_model = get_user_model()
    codename = "change_sellerverification"
    return list(
        user_model.objects.filter(is_active=True, is_staff=True)
        .filter(
            Q(is_superuser=True)
            | Q(user_permissions__codename=codename)
            | Q(groups__permissions__codename=codename)
        )
        .distinct()[:MAX_REVIEWER_NOTIFICATIONS]
    )


def _notify_reviewers(seller: SellerProfile, verification_id: int) -> None:
    link = reverse(
        "admin:marketplace_sellerverification_change", args=[verification_id]
    )
    for reviewer in _reviewers():
        send_notification(
            reviewer,
            "New seller application",
            f"{seller.display_name} applied to become a marketplace seller.",
            url=link,
            action_type="moderation",
            icon="store",
        )


@transaction.atomic
def submit_application(user: CustomUser, data: ApplicationData) -> SellerProfile:
    """Create or update the seller profile and open one pending review.

    A pending, approved or suspended profile cannot be submitted again, so a
    double POST creates exactly one pending review.
    """
    if not data.accepted_terms:
        raise MarketplaceError("Accept the seller terms to apply.")
    if len(data.social_links) > MAX_SOCIAL_LINKS:
        raise MarketplaceError(f"Add at most {MAX_SOCIAL_LINKS} social links.")
    seller = SellerProfile.objects.select_for_update().filter(user=user).first()
    if seller is None:
        seller = SellerProfile(
            user=user,
            display_name=(user.get_full_name() or user.username or "Seller")[:100],
        )
    elif not seller.is_active:
        raise MarketplaceError("This seller profile is deactivated.")
    elif seller.verified:
        raise MarketplaceError("You are already an approved seller.")
    elif seller.verifications.filter(status=SellerVerification.Status.PENDING).exists():
        raise MarketplaceError("Your application is already waiting for review.")
    seller.bio = data.bio.strip()
    seller.website = data.website
    seller.contact_preference = data.contact_preference
    seller.terms_accepted_at = timezone.now()
    seller.save()
    seller.brands.set(data.brands)
    seller.chipsets.set(data.chipsets)
    seller.social_links.all().delete()
    SellerSocialLink.objects.bulk_create(
        SellerSocialLink(seller=seller, platform=link.platform, url=link.url)
        for link in data.social_links
    )
    verification = SellerVerification.objects.create(
        seller=seller, evidence=_evidence(data)
    )
    transaction.on_commit(lambda: _notify_reviewers(seller, verification.pk))
    return seller


def _notify_decision(verification: SellerVerification) -> None:
    approved = verification.status == SellerVerification.Status.APPROVED
    if approved:
        title = "Seller application approved"
        message = (
            "You are an approved seller now. You can mark your uploads as paid "
            "or featured."
        )
        url = reverse("marketplace:dashboard")
    else:
        reason = (
            f" Reason: {verification.review_reason}"
            if verification.review_reason
            else ""
        )
        title = "Seller application not approved"
        message = f"Staff did not approve your application.{reason}"
        url = reverse("marketplace:application_status")
    send_notification(
        verification.seller.user,
        title,
        message,
        level="info" if approved else "warning",
        url=url,
        action_type="moderation",
        icon="store",
    )


@transaction.atomic
def review_verification(
    verification_id: int, *, reviewer: Any, approve: bool, reason: str = ""
) -> SellerVerification:
    verification = (
        SellerVerification.objects.select_for_update()
        .select_related("seller__user")
        .get(pk=verification_id)
    )
    if verification.status != SellerVerification.Status.PENDING:
        raise MarketplaceError("This verification was already reviewed.")
    reason = reason.strip()
    if not approve and not reason:
        raise MarketplaceError("A reason is required to reject an application.")
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
    transaction.on_commit(lambda: _notify_decision(verification))
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
