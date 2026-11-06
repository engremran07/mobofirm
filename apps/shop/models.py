"""apps.shop — Products, package tiers, subscriptions and offline-fulfilled orders.

Online payment processing is not part of this release: orders are requests that
staff mark as paid after receiving payment outside the platform.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class ProductType(models.TextChoices):
    PACKAGE = "package", "Package"
    SUBSCRIPTION = "subscription", "Subscription"
    SERVICE = "service", "Service"


class BillingCycle(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"
    LIFETIME = "lifetime", "Lifetime"


class SubscriptionPlan(models.Model):
    """Recurring subscription tier."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_downloads = models.IntegerField(default=-1, help_text="-1 = unlimited")
    features = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"
        ordering: ClassVar[list[str]] = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class PackageTier(models.Model):
    """Download package with quotas and feature flags enforced at download time."""

    class TierLevel(models.TextChoices):
        FREE = "free", "Free"
        BASIC = "basic", "Basic"
        PRO = "pro", "Pro"
        PREMIUM = "premium", "Premium"
        ENTERPRISE = "enterprise", "Enterprise"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    tier_level = models.CharField(
        max_length=12, choices=TierLevel.choices, default=TierLevel.FREE, db_index=True
    )
    description = models.TextField(blank=True, default="")

    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_quarterly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_lifetime = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    daily_download_limit = models.IntegerField(
        default=5, help_text="Max downloads per 24h (-1 = unlimited)"
    )
    monthly_download_limit = models.IntegerField(
        default=50, help_text="Max downloads per month (-1 = unlimited)"
    )
    max_file_size_mb = models.IntegerField(
        default=500, help_text="Max single file size in MB (-1 = unlimited)"
    )
    daily_bandwidth_mb = models.IntegerField(
        default=2048, help_text="Max bandwidth per 24h in MB (-1 = unlimited)"
    )
    monthly_bandwidth_mb = models.IntegerField(
        default=20480, help_text="Max bandwidth per month in MB (-1 = unlimited)"
    )
    max_download_speed_kbps = models.IntegerField(
        default=0, help_text="Speed cap in KB/s (0 = no limit)"
    )
    max_concurrent_downloads = models.PositiveSmallIntegerField(default=1)

    ad_free = models.BooleanField(default=False, help_text="Skip ad gates")
    priority_queue = models.BooleanField(default=False)
    resume_support = models.BooleanField(default=True)
    api_access = models.BooleanField(default=False)
    early_access = models.BooleanField(default=False)
    direct_links = models.BooleanField(default=False)

    access_official = models.BooleanField(default=True)
    access_engineering = models.BooleanField(default=False)
    access_readback = models.BooleanField(default=False)
    access_modified = models.BooleanField(default=False)

    cooldown_seconds = models.PositiveIntegerField(default=60)

    is_active = models.BooleanField(default=True, db_index=True)
    is_default = models.BooleanField(
        default=False, help_text="Tier used for users without a purchased package"
    )
    is_featured = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    extra_features = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Package Tier"
        verbose_name_plural = "Package Tiers"
        ordering: ClassVar[list[str]] = ["sort_order", "price_monthly"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="shop_single_default_tier",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_tier_level_display()})"

    @property
    def is_unlimited_downloads(self) -> bool:
        return self.daily_download_limit == -1 and self.monthly_download_limit == -1

    @property
    def is_unlimited_bandwidth(self) -> bool:
        return self.daily_bandwidth_mb == -1 and self.monthly_bandwidth_mb == -1


class UserPackage(models.Model):
    """A user's package, linking a user to a PackageTier with usage counters."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRIALING = "trialing", "Trialing"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        SUSPENDED = "suspended", "Suspended"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_packages"
    )
    package = models.ForeignKey(
        PackageTier, on_delete=models.PROTECT, related_name="user_packages"
    )
    billing_cycle = models.CharField(
        max_length=12, choices=BillingCycle.choices, default=BillingCycle.MONTHLY
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    downloads_today = models.PositiveIntegerField(default=0)
    downloads_this_month = models.PositiveIntegerField(default=0)
    bandwidth_today_mb = models.PositiveIntegerField(default=0)
    bandwidth_this_month_mb = models.PositiveIntegerField(default=0)
    last_download_at = models.DateTimeField(null=True, blank=True)
    usage_reset_date = models.DateField(
        null=True, blank=True, help_text="Start of the current monthly usage window"
    )

    started_at = models.DateTimeField(auto_now_add=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "User Package"
        verbose_name_plural = "User Packages"
        ordering: ClassVar[list[str]] = ["-started_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status__in=["active", "trialing"]),
                name="shop_one_live_package_per_user",
            )
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "status"], name="userpkg_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.package} [{self.status}]"


class PackageQuotaOverride(models.Model):
    """Per-user override of specific quota fields (staff-granted exceptions)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="package_quota_overrides",
    )
    daily_download_limit = models.IntegerField(null=True, blank=True)
    monthly_download_limit = models.IntegerField(null=True, blank=True)
    daily_bandwidth_mb = models.IntegerField(null=True, blank=True)
    monthly_bandwidth_mb = models.IntegerField(null=True, blank=True)
    max_download_speed_kbps = models.IntegerField(null=True, blank=True)
    max_concurrent_downloads = models.PositiveSmallIntegerField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_quota_overrides",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Package Quota Override"
        verbose_name_plural = "Package Quota Overrides"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Override for {self.user} (by {self.granted_by})"


class Product(models.Model):
    """Orderable item in the shop."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    product_type = models.CharField(max_length=15, choices=ProductType.choices)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal(0))]
    )
    package_tier = models.ForeignKey(
        PackageTier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
    )
    subscription_plan = models.ForeignKey(
        SubscriptionPlan,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=~Q(product_type="package") | Q(package_tier__isnull=False),
                name="shop_package_product_has_tier",
            ),
            models.CheckConstraint(
                condition=~Q(product_type="subscription")
                | Q(subscription_plan__isnull=False),
                name="shop_subscription_product_has_plan",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.price})"


class Subscription(models.Model):
    """A user's subscription to a plan."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        TRIALING = "trialing", "Trialing"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    started_at = models.DateTimeField(auto_now_add=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        ordering: ClassVar[list[str]] = ["-started_at"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "status"], name="sub_user_status_idx")
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.plan} [{self.status}]"


class Order(models.Model):
    """Order request fulfilled offline: staff confirm payment, then provision."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        OFFLINE = "offline", "Offline (arranged with staff)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    payment_method = models.CharField(
        max_length=12, choices=PaymentMethod.choices, default=PaymentMethod.OFFLINE
    )
    payment_reference = models.CharField(max_length=200, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_orders",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Order #{self.pk} [{self.status}] {self.total} {self.currency}"


class OrderItem(models.Model):
    """Line item within an order."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="order_items"
    )
    billing_cycle = models.CharField(
        max_length=12, choices=BillingCycle.choices, default=BillingCycle.MONTHLY
    )
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"

    def __str__(self) -> str:
        return f"{self.quantity}× {self.product} in Order #{self.order_id}"
