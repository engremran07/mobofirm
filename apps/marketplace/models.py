"""apps.marketplace — Seller profiles, verification and moderated listings.

There is no checkout or payout: listings are showcase entries and purchases are
arranged outside the platform until a payment provider is approved.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.urls import reverse

MAX_BIO_LENGTH = 2000
MAX_DISPLAY_NAME_LENGTH = 100


class SellerProfile(models.Model):
    """Seller identity attached to a user; ``verified`` is set by staff review."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_profile",
    )
    display_name = models.CharField(max_length=MAX_DISPLAY_NAME_LENGTH)
    bio = models.TextField(max_length=MAX_BIO_LENGTH, blank=True, default="")
    verified = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Seller Profile"
        verbose_name_plural = "Seller Profiles"

    def __str__(self) -> str:
        return f"{self.display_name} ({'verified' if self.verified else 'unverified'})"

    def get_absolute_url(self) -> str:
        return reverse("marketplace:seller_profile", kwargs={"pk": self.pk})


class SellerVerification(models.Model):
    """Staff-reviewed verification request; no identity documents are stored."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    seller = models.ForeignKey(
        SellerProfile, on_delete=models.CASCADE, related_name="verifications"
    )
    evidence = models.TextField(
        max_length=2000,
        help_text="Public links or a description that lets staff confirm the seller.",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_seller_verifications",
    )
    review_reason = models.CharField(max_length=255, blank=True, default="")
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Seller Verification"
        verbose_name_plural = "Seller Verifications"
        ordering: ClassVar[list[str]] = ["-submitted_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["seller"],
                condition=Q(status="pending"),
                name="marketplace_one_pending_verification",
            )
        ]

    def __str__(self) -> str:
        return f"Verification for {self.seller} [{self.status}]"


class Listing(models.Model):
    """Showcase listing; visible only after staff approval."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    seller = models.ForeignKey(
        SellerProfile, on_delete=models.CASCADE, related_name="listings"
    )
    firmware = models.ForeignKey(
        "firmwares.OfficialFirmware",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marketplace_listings",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    discount_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    is_featured = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_listings",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=255, blank=True, default="")

    view_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Listing"
        verbose_name_plural = "Listings"
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(discount_price__isnull=True)
                | Q(discount_price__lte=F("price")),
                name="marketplace_discount_not_above_price",
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["status", "is_active"], name="listing_status_active_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} by {self.seller}"

    def get_absolute_url(self) -> str:
        return reverse("marketplace:listing_detail", kwargs={"pk": self.pk})

    @property
    def effective_price(self):
        return self.discount_price if self.discount_price is not None else self.price

    @property
    def is_public(self) -> bool:
        return (
            self.status == self.Status.APPROVED
            and self.is_active
            and self.seller.is_active
            and self.seller.verified
        )
