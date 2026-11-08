"""apps.shop — Download packages, user packages and offline-fulfilled orders.

A :class:`PackageTier` (presented everywhere as a *Package*) is the one definition of
what an account may download: quota per window and item type, devices, extra and
fair-usage caps and perks. :class:`UserPackage` is the one subscription record, and a
package is bought directly through an :class:`Order` (no separate catalogue product).

Online payment processing is not part of this release: orders are requests that
staff mark as paid after receiving payment outside the platform.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from solo.models import SingletonModel

from . import wallet_gateway

DEFAULT_CURRENCY = "USD"
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}
MB_PER_GB = 1024
ITEM_TYPES = ("free", "featured", "paid")
LIFETIME = 0


class WindowKind(models.TextChoices):
    PACKAGE = "package", "Whole package period"
    MONTH = "month", "Calendar month"
    DAY = "day", "Calendar day"


class SizeUnit(models.TextChoices):
    MB = "MB", "MB"
    GB = "GB", "GB"


def format_money(amount: Decimal, currency: str = DEFAULT_CURRENCY) -> str:
    """``$9`` for whole amounts, ``$9.50`` otherwise; unknown currencies get a code."""
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    if amount == amount.to_integral_value():
        return f"{symbol}{int(amount)}"
    return f"{symbol}{amount:.2f}"


def format_size_mb(megabytes: int) -> str:
    """``20 GB`` when the size is a whole number of gigabytes, else ``512 MB``."""
    if megabytes >= MB_PER_GB and megabytes % MB_PER_GB == 0:
        return f"{megabytes // MB_PER_GB} GB"
    return f"{megabytes} MB"


def duration_phrase(days: int) -> str:
    """``lifetime``, ``30 days`` or ``1 year`` for a package duration in days."""
    if days == LIFETIME:
        return "lifetime"
    if days % 365 == 0:
        years = days // 365
        return "year" if years == 1 else f"{years} years"
    return "day" if days == 1 else f"{days} days"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class PackageTier(models.Model):
    """One Download Package: price, duration, bandwidth, item types, caps and perks."""

    # --- Basic info ---
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal(0),
        validators=[MinValueValidator(Decimal(0))],
        verbose_name="Price (USD)",
        help_text=(
            "One-off price in US dollars for the whole duration; 0 for a free package. "
            "The credits price is derived from it with the rate under Credits & pricing."
        ),
    )
    allow_credits = models.BooleanField(
        default=True,
        verbose_name="Payable with credits",
        help_text="Users can pay for this package with wallet credits (price × rate).",
    )
    credit_price_override = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Credits price override",
        help_text=(
            "Optional. Leave empty to use price × rate. Set a number only to charge "
            "a fixed number of credits that ignores the rate."
        ),
    )
    duration_days = models.PositiveIntegerField(
        default=30,
        help_text="How long the package lasts after activation; 0 = lifetime",
    )
    limit_devices = models.BooleanField(
        default=True, help_text="Cap the registered devices per account"
    )
    device_limit = models.PositiveIntegerField(
        default=3, help_text="Registered devices allowed when the device cap is on"
    )
    slot_reset_credits = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        verbose_name="Device slot reset price (credits)",
        help_text=(
            "Credits this package's holders pay to free their registered browsers. "
            "Leave empty to use the global price under Credits economy → Prices "
            "(“Reset registered-browser slots”)."
        ),
    )
    is_public = models.BooleanField(
        default=True, help_text="Listed on the pricing page"
    )
    is_active = models.BooleanField(
        default=True, db_index=True, help_text="Can be ordered and granted"
    )
    is_default = models.BooleanField(
        default=False, help_text="The free package every account without one gets"
    )
    is_popular = models.BooleanField(
        default=False, help_text="Highlighted as the popular choice"
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    # --- Bandwidth ---
    bandwidth_enabled = models.BooleanField(
        default=True, help_text="Limit how much may be downloaded; off = unlimited"
    )
    limit_size = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Total download volume per window, in the unit below (20 + GB = 20 GB); "
            "0 = no size limit"
        ),
    )
    size_unit = models.CharField(
        max_length=2,
        choices=SizeUnit.choices,
        default=SizeUnit.GB,
        help_text="Unit of every size field on this package",
    )
    limit_files = models.PositiveIntegerField(
        default=0,
        help_text="Most new files per window (e.g. 30); 0 = no file limit",
    )
    window = models.CharField(
        max_length=8,
        choices=WindowKind.choices,
        default=WindowKind.MONTH,
        help_text=(
            "The period the file/size limit covers: each day, each calendar month, "
            "or the whole package duration"
        ),
    )

    # --- Item types ---
    applies_to_free = models.BooleanField(
        default=True, help_text="Free files are served and counted against the quota"
    )
    applies_to_featured = models.BooleanField(
        default=False, help_text="Unlocks featured files and counts them in the quota"
    )
    applies_to_paid = models.BooleanField(
        default=False, help_text="Paid files you buy count in the quota (never blocked)"
    )
    paid_discount_percent = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(99)],
        help_text="Discount on individually bought paid files; needs the paid type",
    )
    applies_to_featured_blog = models.BooleanField(
        default=False,
        verbose_name="Featured blog articles",
        help_text="Holders read featured (premium) blog articles in full",
    )
    featured_blog_limit = models.PositiveIntegerField(
        default=0,
        verbose_name="Featured articles per window",
        help_text=(
            "Most featured articles a holder can unlock per window (same window as "
            "the file limit); 0 = unlimited. Re-reading an unlocked article is free."
        ),
    )

    # --- Extra bandwidth ---
    extra_daily_files = models.PositiveIntegerField(
        default=0, help_text="Extra new files usable every day; 0 = none"
    )
    extra_daily_size = models.PositiveIntegerField(
        default=0, help_text="Extra size usable every day, in the size unit; 0 = none"
    )

    # --- Fair usage ---
    fair_daily_files = models.PositiveIntegerField(
        default=0, help_text="Hard cap of new files per day; 0 = none"
    )
    fair_daily_size = models.PositiveIntegerField(
        default=0, help_text="Hard cap of size per day, in the size unit; 0 = none"
    )
    fair_monthly_files = models.PositiveIntegerField(
        default=0, help_text="Hard cap of new files per month; 0 = none"
    )
    fair_monthly_size = models.PositiveIntegerField(
        default=0, help_text="Hard cap of size per month, in the size unit; 0 = none"
    )
    max_concurrent_downloads = models.PositiveSmallIntegerField(
        default=0, help_text="Downloads running at once; 0 = no limit"
    )
    cooldown_seconds = models.PositiveIntegerField(
        default=0, help_text="Wait between two new files; 0 = none"
    )

    # --- Perks ---
    ad_free = models.BooleanField(default=False, help_text="Skip ad gates")
    priority_queue = models.BooleanField(default=False)
    resume_support = models.BooleanField(default=True)
    direct_links = models.BooleanField(default=False)
    early_access = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Package"
        verbose_name_plural = "Packages"
        ordering: ClassVar[list[str]] = ["sort_order", "price", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="shop_single_default_tier",
                violation_error_message="Another package is already the default.",
            ),
            models.CheckConstraint(
                condition=Q(applies_to_free=True)
                | Q(applies_to_featured=True)
                | Q(applies_to_paid=True),
                name="shop_package_has_item_type",
                violation_error_message="Select at least one item type.",
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0),
                name="shop_package_price_not_negative",
                violation_error_message="The price cannot be negative.",
            ),
            models.CheckConstraint(
                condition=Q(limit_devices=False) | Q(device_limit__gte=1),
                name="shop_package_device_limit_positive",
                violation_error_message="A device cap needs at least one device.",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    # --- Size helpers (stored in ``size_unit``, enforced in MB) ---

    def _to_mb(self, value: int) -> int:
        return value * (MB_PER_GB if self.size_unit == SizeUnit.GB else 1)

    @property
    def limit_size_mb(self) -> int:
        return self._to_mb(self.limit_size)

    @property
    def extra_daily_size_mb(self) -> int:
        return self._to_mb(self.extra_daily_size)

    @property
    def fair_daily_size_mb(self) -> int:
        return self._to_mb(self.fair_daily_size)

    @property
    def fair_monthly_size_mb(self) -> int:
        return self._to_mb(self.fair_monthly_size)

    # --- Item types ---

    @property
    def item_types(self) -> tuple[str, ...]:
        flags = (
            ("free", self.applies_to_free),
            ("featured", self.applies_to_featured),
            ("paid", self.applies_to_paid),
        )
        return tuple(name for name, on in flags if on)

    @property
    def is_lifetime(self) -> bool:
        return self.duration_days == LIFETIME

    @property
    def is_metered(self) -> bool:
        """Whether a file or size limit applies at all."""
        return self.bandwidth_enabled and bool(self.limit_files or self.limit_size)

    @property
    def credit_price(self) -> int:
        """Credits this package costs: the override, else ``price × rate`` (0 = n/a).

        Derived on every read, so a rate change re-prices the package at once. The
        conversion is the wallet's single ``to_credits``; without a wallet, or for a free
        or credit-disabled package, credits cannot be used (0).
        """
        if not self.allow_credits:
            return 0
        if self.credit_price_override:
            return int(self.credit_price_override)
        if self.price > 0 and wallet_gateway.available():
            return wallet_gateway.to_credits(self.price)
        return 0

    def credits_for(self, price: Decimal) -> int:
        """Credits for a (promotional) dollar ``price`` of this package; 0 = n/a."""
        if not self.allow_credits or price <= 0 or not wallet_gateway.available():
            return 0
        return wallet_gateway.to_credits(price)

    @property
    def price_with_credits(self) -> str:
        """``$9/mo · 90 credits``: the dollar price and its credits equivalent."""
        owed = self.credit_price
        if not owed:
            return self.price_text
        return f"{self.price_text} · {owed} credits"

    # --- Validation ---

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.bandwidth_enabled and not (self.limit_files or self.limit_size):
            errors["limit_files"] = (
                "Set a file limit or a size limit, or turn bandwidth limits off."
            )
        if self.is_default:
            if self.window == WindowKind.PACKAGE:
                errors["window"] = (
                    "The default package has no period of its own: use a day or "
                    "month window."
                )
            if self.price != 0:
                errors["price"] = "The default package is free: its price must be 0."
            if not self.is_lifetime:
                errors["duration_days"] = (
                    "The default package never expires: set the duration to 0."
                )
            if not self.is_active:
                errors["is_active"] = "The default package must stay active."
        if self.paid_discount_percent and not self.applies_to_paid:
            errors["paid_discount_percent"] = (
                "A discount on paid files needs the paid item type."
            )
        if self.featured_blog_limit and not self.applies_to_featured_blog:
            errors["featured_blog_limit"] = (
                "A featured-article limit needs “Featured blog articles” switched on."
            )
        if self.credit_price_override is not None:
            if not self.allow_credits:
                errors["credit_price_override"] = (
                    "A credits price needs “Payable with credits” switched on."
                )
            elif self.is_default or self.price <= 0:
                errors["credit_price_override"] = (
                    "A free package cannot be bought with credits."
                )
        if (
            self.fair_daily_files
            and self.fair_monthly_files
            and self.fair_monthly_files < self.fair_daily_files
        ):
            errors["fair_monthly_files"] = (
                "The monthly file cap cannot be lower than the daily cap."
            )
        if (
            self.fair_daily_size
            and self.fair_monthly_size
            and self.fair_monthly_size < self.fair_daily_size
        ):
            errors["fair_monthly_size"] = (
                "The monthly size cap cannot be lower than the daily cap."
            )
        if errors:
            raise ValidationError(errors)

    # --- Human summary: the one description shown in admin and on the site ---

    @property
    def currency(self) -> str:
        return DEFAULT_CURRENCY

    @property
    def window_phrase(self) -> str:
        if self.window == WindowKind.DAY:
            return "per day"
        if self.window == WindowKind.MONTH:
            return "per month"
        if self.is_lifetime:
            return "in total"
        return f"per {duration_phrase(self.duration_days)}"

    @property
    def quota_text(self) -> str:
        if not self.is_metered:
            return "Unlimited downloads"
        parts = []
        if self.limit_files:
            parts.append(_plural(self.limit_files, "file"))
        if self.limit_size:
            parts.append(f"{self.limit_size} {self.size_unit}")
        return f"{' / '.join(parts)} {self.window_phrase}"

    @property
    def extra_text(self) -> str:
        parts = []
        if self.extra_daily_files:
            parts.append(_plural(self.extra_daily_files, "file"))
        if self.extra_daily_size:
            parts.append(f"{self.extra_daily_size} {self.size_unit}")
        return f"+{' / '.join(parts)} extra per day" if parts else ""

    @property
    def types_text(self) -> str:
        labels = list(self.item_types)
        if self.applies_to_featured_blog:
            labels.append(
                f"{_plural(self.featured_blog_limit, 'article')} of featured blogs"
                if self.featured_blog_limit
                else "featured blogs"
            )
        return " + ".join(labels)

    @property
    def devices_text(self) -> str:
        if not self.limit_devices:
            return "unlimited devices"
        return _plural(self.device_limit, "device")

    @property
    def price_text(self) -> str:
        if self.price == 0:
            return "Free"
        amount = format_money(self.price, self.currency)
        suffixes = {7: "/wk", 30: "/mo", 365: "/yr"}
        if self.is_lifetime:
            return f"{amount} once"
        return f"{amount}{suffixes.get(self.duration_days, f' / {self.duration_days} days')}"

    @property
    def duration_text(self) -> str:
        if self.is_lifetime:
            return "Lifetime" if self.price else "Always included"
        phrase = duration_phrase(self.duration_days)
        return f"1 {phrase}" if phrase in {"day", "year"} else phrase

    @property
    def perk_labels(self) -> list[str]:
        flags = (
            (self.ad_free, "Ad-free downloads"),
            (self.priority_queue, "Priority queue"),
            (self.resume_support, "Resumable downloads"),
            (self.direct_links, "Direct links"),
            (self.early_access, "Early access"),
        )
        return [label for on, label in flags if on]

    def summary(self) -> str:
        """``30 files / 20 GB per 30 days · free + featured · 5 devices · $9/mo``."""
        return (
            f"{self.quota_text} · {self.types_text} · "
            f"{self.devices_text} · {self.price_text}"
        )


class UserPackage(models.Model):
    """The one subscription record: a user holding a package for a period."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"
        SUSPENDED = "suspended", "Suspended"

    class Source(models.TextChoices):
        ORDER = "order", "Order"
        STAFF = "staff", "Staff grant"
        PROMOTION = "promotion", "Promotion"
        CREDITS = "credits", "Credits"
        DEFAULT = "default", "Default (free package)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_packages"
    )
    package = models.ForeignKey(
        PackageTier, on_delete=models.PROTECT, related_name="user_packages"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    source = models.CharField(
        max_length=12, choices=Source.choices, default=Source.ORDER
    )
    is_baseline = models.BooleanField(
        default=False,
        help_text=(
            "The free default package every account holds from sign-up. It stays "
            "under any package the user buys and applies again when that ends."
        ),
    )
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(
        null=True, blank=True, db_index=True, help_text="Empty = lifetime"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=200, blank=True, default="")
    order = models.ForeignKey(
        "shop.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="user_packages",
        help_text="Order that provisioned this package (refunds cancel it)",
    )

    class Meta:
        verbose_name = "User package"
        verbose_name_plural = "User packages"
        ordering: ClassVar[list[str]] = ["-started_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="active", is_baseline=False),
                name="shop_one_live_package_per_user",
                violation_error_message="This user already has an active package.",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="active", is_baseline=True),
                name="shop_one_baseline_per_user",
                violation_error_message="This user already has the free package.",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "status"], name="userpkg_user_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.package} [{self.status}]"

    def clean(self) -> None:
        if self.expires_at is not None and self.expires_at <= self.started_at:
            raise ValidationError({"expires_at": "The end must be after the start."})

    @property
    def is_live(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class PackageQuotaOverride(models.Model):
    """Staff exception for one user package; empty fields inherit the package."""

    user_package = models.ForeignKey(
        UserPackage, on_delete=models.CASCADE, related_name="quota_overrides"
    )
    limit_files = models.PositiveIntegerField(
        null=True, blank=True, help_text="New files per window; 0 = unlimited"
    )
    limit_size_mb = models.PositiveIntegerField(
        null=True, blank=True, help_text="Size per window in MB; 0 = unlimited"
    )
    device_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Registered devices; 0 = unlimited"
    )
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
        verbose_name = "Quota override"
        verbose_name_plural = "Quota overrides"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Override for {self.user_package.user} (by {self.granted_by})"

    def clean(self) -> None:
        if (
            self.limit_files is None
            and self.limit_size_mb is None
            and self.device_limit is None
        ):
            raise ValidationError("Override at least one limit.")


class UserQuotaOverride(models.Model):
    """A staff exception for one account, whatever package it holds.

    Precedence: this override > the user's package (and its per-package override) >
    the free default package. Every empty field inherits; ``0`` on a limit means
    unlimited. One override can be active per user (``is_active``); it stops applying
    at ``expires_at`` and every change is written to :class:`QuotaOverrideAudit`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_quota_overrides",
    )
    limit_files = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Files per window",
        help_text="New files allowed per window, e.g. 30. 0 = unlimited; empty = inherit.",
    )
    limit_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Data volume per window",
        help_text="Download volume per window in the unit below, e.g. 20 GB. "
        "0 = unlimited; empty = inherit.",
    )
    size_unit = models.CharField(
        max_length=2, choices=SizeUnit.choices, default=SizeUnit.GB
    )
    window = models.CharField(
        max_length=8,
        choices=WindowKind.choices,
        blank=True,
        default="",
        verbose_name="Window",
        help_text="The period the limits cover; empty = the package's window.",
    )
    covers_free = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="Free files",
        help_text="Counted in the quota. Empty = as the package.",
    )
    covers_featured = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="Featured files",
        help_text="Unlocked and counted. Empty = as the package.",
    )
    covers_paid = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="Paid files",
        help_text="Counted in the quota. Empty = as the package.",
    )
    device_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Registered devices allowed. 0 = unlimited; empty = inherit.",
    )
    reason = models.CharField(
        max_length=255,
        help_text="Why this account is an exception (kept in the audit).",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_user_quota_overrides",
    )
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Stops applying then; empty = until revoked."
    )
    is_active = models.BooleanField(
        default=True, help_text="Untick to revoke without deleting the record."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Account quota override"
        verbose_name_plural = "Account quota overrides"
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="shop_one_active_user_override",
                violation_error_message=(
                    "This account already has an active override: edit or revoke it."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"Override for {self.user}"

    @property
    def limit_size_mb(self) -> int | None:
        if self.limit_size is None:
            return None
        return self.limit_size * (MB_PER_GB if self.size_unit == SizeUnit.GB else 1)

    @property
    def is_live(self) -> bool:
        return self.is_active and (
            self.expires_at is None or self.expires_at > timezone.now()
        )

    def summary(self) -> str:
        """``30 files / 20 GB per month · featured on · 5 devices``."""
        parts = []
        quota = []
        if self.limit_files is not None:
            quota.append(
                "unlimited files"
                if not self.limit_files
                else _plural(self.limit_files, "file")
            )
        if self.limit_size is not None:
            quota.append(
                "unlimited data"
                if not self.limit_size
                else f"{self.limit_size} {self.size_unit}"
            )
        if quota:
            window = (
                WindowKind(self.window).label.lower()
                if self.window
                else "per package window"
            )
            parts.append(" / ".join(quota) + f" ({window})")
        elif self.window:
            parts.append(f"window: {WindowKind(self.window).label.lower()}")
        for label, value in (
            ("free", self.covers_free),
            ("featured", self.covers_featured),
            ("paid", self.covers_paid),
        ):
            if value is not None:
                parts.append(f"{label} files {'on' if value else 'off'}")
        if self.device_limit is not None:
            parts.append(
                "unlimited devices"
                if not self.device_limit
                else _plural(self.device_limit, "device")
            )
        return " · ".join(parts) or "No limits changed"

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if not (self.reason or "").strip():
            errors["reason"] = "Explain why this account is an exception."
        if (
            not any(
                value is not None
                for value in (
                    self.limit_files,
                    self.limit_size,
                    self.device_limit,
                    self.covers_free,
                    self.covers_featured,
                    self.covers_paid,
                )
            )
            and not self.window
        ):
            errors["limit_files"] = "Change at least one limit, window or item type."
        if (
            self.expires_at is not None
            and self.expires_at <= timezone.now()
            and self._state.adding
        ):
            errors["expires_at"] = "The expiry must be in the future."
        if errors:
            raise ValidationError(errors)


class QuotaOverrideAudit(models.Model):
    """Append-only record of every account-override change (who, what, when)."""

    class Action(models.TextChoices):
        GRANTED = "granted", "Granted"
        CHANGED = "changed", "Changed"
        REVOKED = "revoked", "Revoked"
        DELETED = "deleted", "Deleted"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reason = models.CharField(max_length=255, blank=True, default="")
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Quota override audit"
        verbose_name_plural = "Quota override audit"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.action} · {self.user_id} · {self.created_at:%Y-%m-%d}"


class Order(models.Model):
    """Order request fulfilled offline: staff confirm payment, then provision."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        OFFLINE = "offline", "Offline (arranged with staff)"
        CREDITS = "credits", "Wallet credits"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders"
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    payment_method = models.CharField(
        max_length=12, choices=PaymentMethod.choices, default=PaymentMethod.OFFLINE
    )
    payment_reference = models.CharField(max_length=200, blank=True, default="")
    credits_spent = models.PositiveIntegerField(
        default=0, help_text="Wallet credits charged for this order (credit payments)"
    )
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
    """Line item: one package or one paid firmware file.

    A firmware line references the file by ``content_type`` + ``object_id`` (the
    typed firmware tables are owned by ``apps.firmwares``). Both kinds snapshot their
    label and price when the request was made; a package line also snapshots the
    duration it will be activated for.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    package = models.ForeignKey(
        PackageTier,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    object_id = models.UUIDField(null=True, blank=True)
    description = models.CharField(
        max_length=255, blank=True, default="", help_text="Snapshot of the item name"
    )
    duration_days = models.PositiveIntegerField(
        default=0, help_text="Package lines: activation length in days; 0 = lifetime"
    )
    topup_credits = models.PositiveIntegerField(
        default=0, help_text="Credit top-up lines: credits added to the wallet"
    )
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "Order item"
        verbose_name_plural = "Order items"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=(
                    Q(package__isnull=False)
                    & Q(content_type__isnull=True)
                    & Q(object_id__isnull=True)
                    & Q(topup_credits=0)
                )
                | (
                    Q(package__isnull=True)
                    & Q(content_type__isnull=False)
                    & Q(object_id__isnull=False)
                    & Q(topup_credits=0)
                )
                | (
                    Q(package__isnull=True)
                    & Q(content_type__isnull=True)
                    & Q(object_id__isnull=True)
                    & Q(topup_credits__gt=0)
                ),
                name="shop_order_item_package_file_or_topup",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["content_type", "object_id"], name="shop_item_file_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} in Order #{self.order_id}"

    @property
    def is_file(self) -> bool:
        return self.content_type_id is not None

    @property
    def is_topup(self) -> bool:
        return self.topup_credits > 0

    @property
    def label(self) -> str:
        package = self.package
        return self.description or (package.name if package else "")


class QuotaConsumption(models.Model):
    """One file that counted against a user's allowance, at most once per file.

    The unique constraint makes charging idempotent: serving the same file again,
    or two concurrent requests for it, can never consume the allowance twice.
    ``reason`` is the file's item type, which selects the bucket it is counted in.
    """

    class Reason(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "Paid"
        FEATURED = "featured", "Featured"
        PURCHASED = "purchased", "Bought with credits (not counted)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quota_consumptions",
    )
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.UUIDField()
    reason = models.CharField(max_length=10, choices=Reason.choices)
    size_mb = models.PositiveIntegerField(default=0)
    consumed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Quota consumption"
        verbose_name_plural = "Quota consumptions"
        ordering: ClassVar[list[str]] = ["-consumed_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "content_type", "object_id"],
                name="shop_one_consumption_per_file",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "consumed_at"], name="shop_quota_user_time_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.reason} · {self.object_id}"


class ShopSettings(SingletonModel):
    """Shop-wide prices that are not tied to one package or file."""

    featured_file_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal(0))],
        verbose_name="Featured file price (USD)",
        help_text=(
            "What an account without a package pays to unlock ONE featured file for "
            "good. The credits price is this × the rate under Credits & pricing. "
            "0 = featured files cannot be bought one by one. Package holders never "
            "pay it."
        ),
    )

    class Meta:
        verbose_name = "Shop settings"
        verbose_name_plural = "Shop settings"

    def __str__(self) -> str:
        return "Shop settings"


class FeaturedBlogPurchase(models.Model):
    """A featured blog article bought with credits: lifetime access for one user.

    ``post_id`` is a plain integer on purpose: the blog owns posts and the shop never
    imports them. ``key`` is the caller's idempotency key (one spend per key) and
    ``credits`` the amount charged at the wallet rate when it was bought.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="featured_blog_purchases",
    )
    post_id = models.PositiveIntegerField(db_index=True)
    credits = models.PositiveIntegerField()
    key = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Featured blog purchase"
        verbose_name_plural = "Featured blog purchases"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "post_id"], name="shop_one_blog_purchase_per_post"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · post {self.post_id} · {self.credits} credits"


class FeaturedBlogUnlock(models.Model):
    """A featured article a package holder unlocked from the package quota.

    Counted once per user and article, like :class:`QuotaConsumption` for files:
    re-reading it never consumes the allowance again.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="featured_blog_unlocks",
    )
    post_id = models.PositiveIntegerField()
    unlocked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Featured blog unlock"
        verbose_name_plural = "Featured blog unlocks"
        ordering: ClassVar[list[str]] = ["-unlocked_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "post_id"], name="shop_one_blog_unlock_per_post"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["user", "unlocked_at"], name="shop_blogunlock_user_time_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · post {self.post_id}"
