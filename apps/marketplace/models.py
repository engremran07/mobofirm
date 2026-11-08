"""apps.marketplace — Seller onboarding, verification, earnings and moderated listings.

Online checkout is not available: showcase listings are arranged through staff.
Sales of paid firmware files credit the seller's wallet (``apps.wallet``) when
that app is installed; until then earnings are recorded and credited later.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.urls import reverse

from .validators import MAX_URL_LENGTH, validate_https_url_field

MAX_BIO_LENGTH = 2000
MAX_DISPLAY_NAME_LENGTH = 100
MAX_SOCIAL_LINKS = 6
MAX_SPECIALITIES = 10


class ContactPreference(models.TextChoices):
    SOCIAL = "social", "Message me on one of my social links"
    WEBSITE = "website", "Use my website"
    STAFF = "staff", "Contact me through site staff"


class SocialPlatform(models.TextChoices):
    YOUTUBE = "youtube", "YouTube"
    TELEGRAM = "telegram", "Telegram"
    FACEBOOK = "facebook", "Facebook"
    X = "x", "X"
    INSTAGRAM = "instagram", "Instagram"
    TIKTOK = "tiktok", "TikTok"
    GITHUB = "github", "GitHub"
    XDA = "xda", "XDA"
    OTHER = "other", "Other"


class SellerProfile(models.Model):
    """Seller identity attached to a user; ``verified`` is set by staff review."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_profile",
    )
    display_name = models.CharField(max_length=MAX_DISPLAY_NAME_LENGTH)
    bio = models.TextField(
        max_length=MAX_BIO_LENGTH,
        blank=True,
        default="",
        help_text="What the seller offers and what makes their work unique.",
    )
    website = models.URLField(
        max_length=MAX_URL_LENGTH,
        blank=True,
        default="",
        validators=[validate_https_url_field],
    )
    contact_preference = models.CharField(
        max_length=10,
        choices=ContactPreference.choices,
        default=ContactPreference.SOCIAL,
    )
    brands = models.ManyToManyField(
        "firmwares.Brand", blank=True, related_name="marketplace_sellers"
    )
    chipsets = models.ManyToManyField(
        "firmwares.Chipset", blank=True, related_name="marketplace_sellers"
    )
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
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


class SellerSocialLink(models.Model):
    """A public page where the seller posts their work (https only)."""

    seller = models.ForeignKey(
        SellerProfile, on_delete=models.CASCADE, related_name="social_links"
    )
    platform = models.CharField(
        max_length=12, choices=SocialPlatform.choices, default=SocialPlatform.OTHER
    )
    url = models.URLField(
        max_length=MAX_URL_LENGTH, validators=[validate_https_url_field]
    )

    class Meta:
        verbose_name = "Seller Social Link"
        verbose_name_plural = "Seller Social Links"
        ordering: ClassVar[list[str]] = ["pk"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["seller", "url"], name="marketplace_unique_seller_link"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_platform_display()}: {self.url}"


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


class SellerEarning(models.Model):
    """One purchased paid file credited (or waiting to be credited) to a seller.

    ``(order_ref, firmware_ref)`` is the idempotency key: a repeated shop event
    for the same order line never creates a second earning or a second credit.
    """

    class Status(models.TextChoices):
        AWAITING_WALLET = "awaiting_wallet", "Waiting for the credit wallet"
        CREDITED = "credited", "Credited"
        REVERSED = "reversed", "Reversed (refund)"
        REVERSAL_FAILED = "reversal_failed", "Refund needs staff attention"

    seller = models.ForeignKey(
        SellerProfile, on_delete=models.PROTECT, related_name="earnings"
    )
    order_ref = models.CharField(max_length=64)
    firmware_ref = models.CharField(
        max_length=120, help_text="app_label.model:object id of the purchased file"
    )
    firmware_title = models.CharField(max_length=200, blank=True, default="")
    gross_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Price of the file in currency units, for reference.",
    )
    gross_credits = models.PositiveIntegerField(default=0)
    commission_credits = models.PositiveIntegerField(default=0)
    net_credits = models.PositiveIntegerField(
        default=0, help_text="Credits the seller receives."
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.AWAITING_WALLET,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Seller Earning"
        verbose_name_plural = "Seller Earnings"
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["order_ref", "firmware_ref"],
                name="marketplace_one_earning_per_order_file",
            )
        ]

    def __str__(self) -> str:
        return f"{self.firmware_title or self.firmware_ref}: {self.net_credits} credits"

    @property
    def credit_key(self) -> str:
        return f"marketplace:earning:{self.order_ref}:{self.firmware_ref}"

    @property
    def reversal_key(self) -> str:
        return f"marketplace:earning-reversal:{self.order_ref}:{self.firmware_ref}"
