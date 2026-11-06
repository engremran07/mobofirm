"""Shop business logic: offline order fulfilment, packages and download quotas.

Online payments are intentionally not implemented. ``ONLINE_PAYMENTS_ENABLED`` is a
fixed ``False`` so every UI and API can show the disabled state; enabling a payment
provider is a separate, owner-approved change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    BillingCycle,
    Order,
    OrderItem,
    PackageQuotaOverride,
    PackageTier,
    Product,
    ProductType,
    Subscription,
    UserPackage,
)

ONLINE_PAYMENTS_ENABLED = False

# Firmware classifications a package tier can allow or deny; other types are
# governed by quotas only.
TIER_GATED_TYPES = frozenset({"official", "engineering", "readback", "modified"})

CYCLE_DURATION = {
    BillingCycle.MONTHLY: timedelta(days=30),
    BillingCycle.QUARTERLY: timedelta(days=90),
    BillingCycle.YEARLY: timedelta(days=365),
    BillingCycle.LIFETIME: None,
}


class OrderError(ValueError):
    """Raised when an order cannot be created or transitioned."""


def _expiry_for(cycle: str):
    duration = CYCLE_DURATION[BillingCycle(cycle)]
    return None if duration is None else timezone.now() + duration


@transaction.atomic
def create_order(user: Any, product: Product, billing_cycle: str = "monthly") -> Order:
    """Create a pending offline order for one active product."""
    if not product.is_active:
        raise OrderError("This product is not available.")
    if billing_cycle not in BillingCycle.values:
        raise OrderError("Unknown billing cycle.")
    duplicate = Order.objects.filter(
        user=user,
        status=Order.Status.PENDING,
        items__product=product,
        items__billing_cycle=billing_cycle,
    ).first()
    if duplicate is not None:
        return duplicate
    order = Order.objects.create(user=user, total=product.price)
    OrderItem.objects.create(
        order=order,
        product=product,
        billing_cycle=billing_cycle,
        quantity=1,
        unit_price=product.price,
        line_total=product.price,
    )
    return order


@transaction.atomic
def activate_package(
    user: Any, tier: PackageTier, billing_cycle: str = "monthly", reference: str = ""
) -> UserPackage:
    """Make ``tier`` the user's one live package, expiring any previous one."""
    UserPackage.objects.filter(
        user=user,
        status__in=[UserPackage.Status.ACTIVE, UserPackage.Status.TRIALING],
    ).update(status=UserPackage.Status.EXPIRED)
    return UserPackage.objects.create(
        user=user,
        package=tier,
        billing_cycle=billing_cycle,
        status=UserPackage.Status.ACTIVE,
        expires_at=_expiry_for(billing_cycle),
        usage_reset_date=timezone.localdate(),
        payment_reference=reference,
    )


@transaction.atomic
def cancel_package(user_package: UserPackage) -> UserPackage:
    user_package.status = UserPackage.Status.CANCELLED
    user_package.cancelled_at = timezone.now()
    user_package.save(update_fields=["status", "cancelled_at"])
    return user_package


def _provision_item(order: Order, item: OrderItem) -> None:
    product = item.product
    if product.product_type == ProductType.PACKAGE and product.package_tier:
        activate_package(
            order.user,
            product.package_tier,
            item.billing_cycle,
            reference=order.payment_reference,
        )
    elif product.product_type == ProductType.SUBSCRIPTION and product.subscription_plan:
        Subscription.objects.create(
            user=order.user,
            plan=product.subscription_plan,
            expires_at=_expiry_for(item.billing_cycle),
        )


@transaction.atomic
def mark_order_paid(order_id: int, *, staff_user: Any, reference: str = "") -> Order:
    """Confirm offline payment and provision the order exactly once.

    Locks the order row so concurrent or repeated confirmations are no-ops.
    """
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.Status.PAID:
        return order
    if order.status != Order.Status.PENDING:
        raise OrderError(
            f"A {order.get_status_display().lower()} order cannot be paid."
        )
    now = timezone.now()
    order.status = Order.Status.PAID
    order.paid_at = now
    order.fulfilled_at = now
    order.processed_by = staff_user
    order.payment_reference = reference[:200]
    order.save()
    for item in order.items.select_related(
        "product", "product__package_tier", "product__subscription_plan"
    ):
        _provision_item(order, item)
    return order


@transaction.atomic
def cancel_order(order_id: int, *, staff_user: Any) -> Order:
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.Status.CANCELLED:
        return order
    if order.status != Order.Status.PENDING:
        raise OrderError("Only pending orders can be cancelled.")
    order.status = Order.Status.CANCELLED
    order.processed_by = staff_user
    order.save(update_fields=["status", "processed_by", "updated_at"])
    return order


def expire_overdue_packages() -> dict[str, int]:
    """Expire packages and subscriptions whose end date passed. Idempotent."""
    now = timezone.now()
    packages = UserPackage.objects.filter(
        status__in=[UserPackage.Status.ACTIVE, UserPackage.Status.TRIALING],
        expires_at__lt=now,
    ).update(status=UserPackage.Status.EXPIRED)
    subscriptions = Subscription.objects.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIALING],
        expires_at__lt=now,
    ).update(status=Subscription.Status.EXPIRED)
    return {"packages": packages, "subscriptions": subscriptions}


# --- Download quotas -------------------------------------------------------


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str = ""


def get_active_package(user: Any) -> UserPackage | None:
    return (
        UserPackage.objects.select_related("package")
        .filter(
            user=user,
            status__in=[UserPackage.Status.ACTIVE, UserPackage.Status.TRIALING],
        )
        .first()
    )


def ensure_package(user: Any) -> UserPackage | None:
    """Return the user's live package, assigning the default tier if they have none."""
    package = get_active_package(user)
    if package is not None:
        return package
    default_tier = PackageTier.objects.filter(is_default=True, is_active=True).first()
    if default_tier is None:
        return None
    return UserPackage.objects.create(
        user=user,
        package=default_tier,
        status=UserPackage.Status.ACTIVE,
        usage_reset_date=timezone.localdate(),
    )


def _roll_usage_windows(package: UserPackage) -> None:
    today = timezone.localdate()
    if (
        package.last_download_at
        and timezone.localtime(package.last_download_at).date() < today
    ):
        package.downloads_today = 0
        package.bandwidth_today_mb = 0
    reset = package.usage_reset_date
    if reset is None or (reset.year, reset.month) != (today.year, today.month):
        package.downloads_this_month = 0
        package.bandwidth_this_month_mb = 0
        package.usage_reset_date = today


def _limit(
    package: UserPackage, override: PackageQuotaOverride | None, name: str
) -> int:
    value = getattr(override, name, None) if override else None
    return getattr(package.package, name) if value is None else value


def check_download_quota(user: Any, *, size_mb: int = 0) -> QuotaDecision:
    """Read-only quota decision for a prospective download."""
    package = ensure_package(user)
    if package is None:
        return QuotaDecision(False, "No package is available for this account.")
    _roll_usage_windows(package)
    now = timezone.now()
    override = (
        PackageQuotaOverride.objects.filter(user=user)
        .exclude(expires_at__lt=now)
        .order_by("-created_at")
        .first()
    )
    tier = package.package

    if tier.max_file_size_mb >= 0 and size_mb > tier.max_file_size_mb:
        return QuotaDecision(
            False, f"File exceeds the {tier.max_file_size_mb} MB limit."
        )
    checks = (
        (
            "daily_download_limit",
            package.downloads_today,
            1,
            "Daily download limit reached.",
        ),
        (
            "monthly_download_limit",
            package.downloads_this_month,
            1,
            "Monthly download limit reached.",
        ),
        (
            "daily_bandwidth_mb",
            package.bandwidth_today_mb,
            size_mb,
            "Daily bandwidth limit reached.",
        ),
        (
            "monthly_bandwidth_mb",
            package.bandwidth_this_month_mb,
            size_mb,
            "Monthly bandwidth limit reached.",
        ),
    )
    for name, used, increment, message in checks:
        limit = _limit(package, override, name)
        if limit >= 0 and used + increment > limit:
            return QuotaDecision(False, message)
    return QuotaDecision(True)


def firmware_type_allowed(user: Any, firmware_type: str) -> bool:
    """Whether the user's tier may download this firmware classification."""
    package = ensure_package(user)
    if package is None:
        return False
    flag = {
        "official": package.package.access_official,
        "engineering": package.package.access_engineering,
        "readback": package.package.access_readback,
        "modified": package.package.access_modified,
    }
    return flag.get(firmware_type, False)


@transaction.atomic
def record_download(user: Any, *, size_mb: int = 0) -> UserPackage | None:
    """Increment usage counters atomically after a download was granted."""
    package = ensure_package(user)
    if package is None:
        return None
    package = UserPackage.objects.select_for_update().get(pk=package.pk)
    _roll_usage_windows(package)
    package.save(
        update_fields=[
            "downloads_today",
            "bandwidth_today_mb",
            "downloads_this_month",
            "bandwidth_this_month_mb",
            "usage_reset_date",
        ]
    )
    UserPackage.objects.filter(pk=package.pk).update(
        downloads_today=F("downloads_today") + 1,
        downloads_this_month=F("downloads_this_month") + 1,
        bandwidth_today_mb=F("bandwidth_today_mb") + size_mb,
        bandwidth_this_month_mb=F("bandwidth_this_month_mb") + size_mb,
        last_download_at=timezone.now(),
    )
    package.refresh_from_db()
    return package


@dataclass(frozen=True)
class UsageMeter:
    """One quota line: ``limit`` of -1 means unlimited."""

    label: str
    used: int
    limit: int
    unit: str = ""

    @property
    def unlimited(self) -> bool:
        return self.limit < 0

    @property
    def percent(self) -> int:
        if self.unlimited or self.limit == 0:
            return 0 if self.unlimited else 100
        return min(100, round(self.used / self.limit * 100))

    @property
    def text(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        if self.unlimited:
            return f"{self.used}{unit} used · unlimited"
        return f"{self.used} of {self.limit}{unit}"

    @property
    def dom_id(self) -> str:
        return "meter-" + self.label.lower().replace(" ", "-")


@dataclass(frozen=True)
class EntitlementSummary:
    """Read-only view of what an account may download; never writes to the database."""

    tier: PackageTier | None
    package: UserPackage | None
    meters: list[UsageMeter]
    type_access: list[tuple[str, bool]]
    enforced: bool

    @property
    def is_default_tier(self) -> bool:
        return self.package is None and self.tier is not None


def _displayed_usage(package: UserPackage | None) -> dict[str, int]:
    """Current counters with the same day/month rollover rules, without saving."""
    if package is None:
        return dict.fromkeys(
            (
                "downloads_today",
                "downloads_this_month",
                "bandwidth_today_mb",
                "bandwidth_this_month_mb",
            ),
            0,
        )
    today = timezone.localdate()
    usage = {
        "downloads_today": package.downloads_today,
        "downloads_this_month": package.downloads_this_month,
        "bandwidth_today_mb": package.bandwidth_today_mb,
        "bandwidth_this_month_mb": package.bandwidth_this_month_mb,
    }
    if (
        package.last_download_at
        and timezone.localtime(package.last_download_at).date() < today
    ):
        usage["downloads_today"] = usage["bandwidth_today_mb"] = 0
    reset = package.usage_reset_date
    if reset is None or (reset.year, reset.month) != (today.year, today.month):
        usage["downloads_this_month"] = usage["bandwidth_this_month_mb"] = 0
    return usage


def entitlement_summary(user: Any) -> EntitlementSummary:
    """Quota meters and category access for the account's live or default package."""
    package = get_active_package(user)
    tier = (
        package.package
        if package is not None
        else PackageTier.objects.filter(is_default=True, is_active=True).first()
    )
    meters: list[UsageMeter] = []
    access: list[tuple[str, bool]] = []
    if tier is not None:
        override = (
            PackageQuotaOverride.objects.filter(user=user)
            .exclude(expires_at__lt=timezone.now())
            .order_by("-created_at")
            .first()
        )
        usage = _displayed_usage(package)

        def limit(name: str) -> int:
            value = getattr(override, name, None) if override else None
            return getattr(tier, name) if value is None else value

        meters = [
            UsageMeter(
                "Downloads today",
                usage["downloads_today"],
                limit("daily_download_limit"),
            ),
            UsageMeter(
                "Downloads this month",
                usage["downloads_this_month"],
                limit("monthly_download_limit"),
            ),
            UsageMeter(
                "Bandwidth today",
                usage["bandwidth_today_mb"],
                limit("daily_bandwidth_mb"),
                "MB",
            ),
            UsageMeter(
                "Bandwidth this month",
                usage["bandwidth_this_month_mb"],
                limit("monthly_bandwidth_mb"),
                "MB",
            ),
        ]
        access = [
            ("Official", tier.access_official),
            ("Engineering", tier.access_engineering),
            ("Readback", tier.access_readback),
            ("Modified", tier.access_modified),
        ]
    return EntitlementSummary(
        tier=tier,
        package=package,
        meters=meters,
        type_access=access,
        enforced=bool(getattr(settings, "SHOP_ENTITLEMENTS_ENFORCED", False)),
    )


def shop_stats() -> dict[str, Any]:
    """Aggregate counters for staff dashboards."""
    from django.db.models import Sum

    paid = Order.objects.filter(status=Order.Status.PAID)
    return {
        "pending_orders": Order.objects.filter(status=Order.Status.PENDING).count(),
        "paid_orders": paid.count(),
        "revenue": paid.aggregate(total=Sum("total"))["total"] or Decimal(0),
        "active_packages": UserPackage.objects.filter(
            status=UserPackage.Status.ACTIVE
        ).count(),
    }
