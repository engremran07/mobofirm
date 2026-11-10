"""Per-file entitlements and download quotas: the one resolver behind every limit.

This is the contract the firmware download gate (``apps.firmwares.access``) builds
on. Firmware is referenced through ``ContentType`` + UUID because the typed firmware
tables are owned by ``apps.firmwares``; nothing here imports its models at import time.

Resolution (:func:`effective_entitlements`)
-------------------------------------------
free default package → the user's live package (replaces it) → staff override on that
package → registered modifiers (campaigns). ``Entitlements.sources`` explains each step.

Rules
-----
* Item types: ``free`` files need no entitlement, ``featured`` files need a package
  that covers featured files, ``paid`` files need a paid order for that exact file
  (:func:`has_purchased`) and are never blocked by a quota.
* Bandwidth is metered per package window (whole package period, calendar month or
  day) and only for the item types the package covers. The package's extra daily
  allowance is added on top of the window allowance, every day.
* Fair usage (daily/monthly caps) and the cooldown protect the service; they count
  free and featured files only and apply whatever the bandwidth settings are.
* Serving a file again never charges again: ``QuotaConsumption`` is unique per
  (user, file), which also makes concurrent requests safe. Bought paid files are
  recorded too ("Paid · counted in quota") when the package covers paid files.
* Accounts without a package get the default package (``is_default``) or, when none
  is active, a transient free package built from the ``SHOP_FREE_*`` settings.
* Online payments are disabled: staff mark a requested order paid, which activates
  the entitlement; staff refunds revoke it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone

from . import request_cache
from .models import (
    ITEM_TYPES,
    Order,
    OrderItem,
    PackageQuotaOverride,
    PackageTier,
    QuotaConsumption,
    ShopSettings,
    UserPackage,
    UserQuotaOverride,
    WindowKind,
    format_size_mb,
)
from .services import OrderError, lock_user

BYTES_PER_MB = 1024 * 1024
UNLIMITED = -1
FAIR_USAGE_TYPES = ("free", "featured")
WINDOW_LABELS: dict[str, str] = {
    WindowKind.DAY.value: "today",
    WindowKind.MONTH.value: "this month",
    WindowKind.PACKAGE.value: "in this package",
}


class EntitlementError(Exception):
    """The user may not be served this file."""


class QuotaExceeded(EntitlementError):
    """The allowance has no room left for another new file."""


class NotEntitled(EntitlementError):
    """The file needs a purchase or package the user does not hold."""


class PurchaseError(OrderError):
    """A paid-file purchase request was rejected."""


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class QuotaSnapshot:
    """One allowance meter: ``limit``/``remaining`` are ``None`` when unlimited.

    ``kind`` is ``files`` or ``size``; sizes are in MB. ``resets_at`` is ``None`` for a
    lifetime package window that never resets.
    """

    limit: int | None
    used: int
    remaining: int | None
    window_label: str
    resets_at: datetime | None
    package_name: str
    unlimited: bool
    kind: str = "files"
    title: str = ""

    @property
    def label(self) -> str:
        if self.title:
            return self.title
        noun = "New files" if self.kind == "files" else "Download size"
        return f"{noun} {self.window_label}"

    @property
    def dom_id(self) -> str:
        slug = "".join(c if c.isalnum() else "-" for c in self.label.lower())
        return "quota-meter-" + slug

    @property
    def percent(self) -> int:
        if self.limit is None:
            return 0
        if self.limit == 0:
            return 100
        return min(100, round(self.used / self.limit * 100))

    def _fmt(self, value: int) -> str:
        return str(value) if self.kind == "files" else format_size_mb(value)

    @property
    def remaining_text(self) -> str:
        if self.remaining is None:
            return "Unlimited"
        return f"{self._fmt(self.remaining)} left"

    @property
    def text(self) -> str:
        if self.limit is None:
            return f"{self._fmt(self.used)} used {self.window_label} · unlimited"
        return f"{self._fmt(self.used)} of {self._fmt(self.limit)} used {self.window_label}"


@dataclass(frozen=True)
class Limits:
    """Resolved caps; ``-1`` means unlimited, ``0`` means none for the soft settings.

    ``files``/``size_mb`` are the window allowance (counted for ``metered`` item
    types only); the ``fair_*`` caps and cooldown apply to free and featured files.
    """

    window: str
    files: int
    size_mb: int
    extra_daily_files: int
    extra_daily_size_mb: int
    fair_daily_files: int
    fair_monthly_files: int
    fair_daily_mb: int
    fair_monthly_mb: int
    max_concurrent: int
    cooldown_seconds: int
    metered: frozenset[str]


@dataclass(frozen=True)
class Perks:
    ad_free: bool = False
    priority_queue: bool = False
    resume_support: bool = False
    direct_links: bool = False
    early_access: bool = False


@dataclass(frozen=True)
class Entitlements:
    """Everything an account is entitled to, resolved in one place.

    ``download_quota`` is the file allowance per ``window`` (``None`` when
    ``unlimited``) and ``device_limit`` the number of registered devices (``None`` =
    unlimited); both come from the same package definition. ``item_types`` are the
    types the package covers and ``sources`` explains each contribution in display
    order, e.g. ``("Free tier: 10 files per day, 3 devices", "Campaign X +10")``.
    """

    download_quota: int | None
    window: str
    unlimited: bool
    device_limit: int | None
    can_access_featured: bool
    can_upload_paid: bool
    package_name: str
    sources: tuple[str, ...]
    limits: Limits
    window_start: datetime
    resets_at: datetime | None
    perks: Perks
    item_types: tuple[str, ...]
    paid_discount_percent: int
    plan_summary: str
    package: UserPackage | None = None
    tier: PackageTier | None = None
    slot_reset_credits: int | None = None
    featured_blog: bool = False
    featured_blog_limit: int = 0

    @property
    def devices_text(self) -> str:
        if self.device_limit is None:
            return "unlimited devices"
        return f"{self.device_limit} device{'' if self.device_limit == 1 else 's'}"


EntitlementModifier = Callable[[Any, Entitlements], Entitlements]
_modifiers: list[EntitlementModifier] = []


def _authenticated(user: Any) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False))


def file_ref(firmware: Any) -> tuple[ContentType, Any]:
    """The (content type, UUID) pair that identifies a typed firmware row."""
    return (
        ContentType.objects.get_for_model(firmware, for_concrete_model=False),
        firmware.pk,
    )


def _access_tier(firmware: Any) -> str:
    return str(getattr(firmware, "access_tier", "") or QuotaConsumption.Reason.FREE)


file_access_tier = _access_tier


def _size_mb(firmware: Any) -> int:
    return math.ceil((getattr(firmware, "file_size", 0) or 0) / BYTES_PER_MB)


# --- Packages and purchases -------------------------------------------------


def active_package(user: Any) -> UserPackage | None:
    """The user's live, unexpired purchased/granted package.

    The free baseline record (``is_baseline``) is not returned: the default package is
    applied by the resolver itself.
    """
    return effective_entitlements(user).package


def has_purchased(user: Any, firmware: Any) -> bool:
    """Whether a paid (and not refunded) order covers this exact file."""
    if not _authenticated(user):
        return False
    content_type, object_id = file_ref(firmware)
    return OrderItem.objects.filter(
        order__user=user,
        order__status=Order.Status.PAID,
        content_type=content_type,
        object_id=object_id,
    ).exists()


def featured_file_price() -> Decimal | None:
    """USD price of one featured file bought on its own (``None`` = not for sale)."""
    price = ShopSettings.get_solo().featured_file_price
    return price if price > 0 else None


def price_for(user: Any, firmware: Any) -> Decimal | None:
    """What this user pays for one file, in USD.

    Paid files cost their staff-approved price less the package's paid-file discount;
    a featured file bought on its own costs the shop-wide featured file price. Either
    is converted to credits only by ``apps.wallet.api.to_credits``.
    """
    if _access_tier(firmware) == QuotaConsumption.Reason.FEATURED:
        return featured_file_price()
    price = getattr(firmware, "approved_price", None)
    if price is None:
        return None
    amount = Decimal(price)
    discount = effective_entitlements(user).paid_discount_percent
    if discount:
        amount = amount * (100 - discount) / 100
    return max(Decimal("0.01"), amount.quantize(Decimal("0.01"), ROUND_HALF_UP))


@transaction.atomic
def request_purchase(user: Any, firmware: Any) -> Order:
    """Create (or return the open) offline order for one paid file.

    Staff confirm payment out of band and mark the order paid; that activates the
    entitlement. The price is the staff-approved price (less the package's paid-file
    discount), snapshotted on the line.
    """
    if not _authenticated(user):
        raise PurchaseError("Sign in to request a purchase.")
    if _access_tier(firmware) != QuotaConsumption.Reason.PAID:
        raise PurchaseError("This file is not sold; it does not need a purchase.")
    price = getattr(firmware, "approved_price", None)
    if price is None or Decimal(price) <= 0:
        raise PurchaseError(
            "This file has no approved price yet, so it cannot be bought."
        )
    if not (
        getattr(firmware, "is_active", False)
        and getattr(firmware, "is_verified", False)
    ):
        raise PurchaseError("This file is not available.")
    if getattr(firmware, "uploader_id", None) == user.pk:
        raise PurchaseError("You uploaded this file; it is already yours.")

    lock_user(user)
    if has_purchased(user, firmware):
        raise PurchaseError("You already own this file.")
    content_type, object_id = file_ref(firmware)
    open_order = Order.objects.filter(
        user=user,
        status=Order.Status.PENDING,
        items__content_type=content_type,
        items__object_id=object_id,
    ).first()
    if open_order is not None:
        return open_order

    amount = price_for(user, firmware)
    if amount is None:
        raise PurchaseError("This file has no approved price yet.")
    order = Order.objects.create(user=user, total=amount)
    OrderItem.objects.create(
        order=order,
        content_type=content_type,
        object_id=object_id,
        description=str(getattr(firmware, "original_file_name", ""))[:255],
        unit_price=amount,
        line_total=amount,
    )
    return order


# --- Resolution --------------------------------------------------------------


def _free_package() -> PackageTier:
    """The default package, else a transient free one from the SHOP_FREE_* settings."""
    tier = PackageTier.objects.filter(is_default=True, is_active=True).first()
    if tier is not None:
        return tier
    devices = settings.SHOP_FREE_DEVICE_LIMIT
    return PackageTier(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        limit_devices=devices > 0,
        device_limit=max(devices, 0),
        bandwidth_enabled=True,
        limit_files=max(settings.SHOP_FREE_DAILY_DOWNLOADS, 0),
        window=WindowKind.DAY,
        applies_to_free=True,
        fair_monthly_files=max(settings.SHOP_FREE_MONTHLY_DOWNLOADS, 0),
    )


def _cap(value: int) -> int:
    return value if value > 0 else UNLIMITED


def _limits(tier: PackageTier) -> Limits:
    metered = tier.is_metered
    return Limits(
        window=tier.window,
        files=_cap(tier.limit_files) if metered else UNLIMITED,
        size_mb=_cap(tier.limit_size_mb) if metered else UNLIMITED,
        extra_daily_files=tier.extra_daily_files,
        extra_daily_size_mb=tier.extra_daily_size_mb,
        fair_daily_files=_cap(tier.fair_daily_files),
        fair_monthly_files=_cap(tier.fair_monthly_files),
        fair_daily_mb=_cap(tier.fair_daily_size_mb),
        fair_monthly_mb=_cap(tier.fair_monthly_size_mb),
        max_concurrent=tier.max_concurrent_downloads,
        cooldown_seconds=tier.cooldown_seconds,
        metered=frozenset(tier.item_types) if metered else frozenset(),
    )


def _live_package(user: Any) -> UserPackage | None:
    return (
        UserPackage.objects.select_related("package")
        .filter(user=user, status=UserPackage.Status.ACTIVE, is_baseline=False)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .order_by("-started_at")
        .first()
    )


def _is_seller(user: Any) -> bool:
    """Whether the user is an approved marketplace seller (False without the app)."""
    if not django_apps.is_installed("apps.marketplace"):
        return False
    from apps.marketplace.services import is_active_seller

    return bool(is_active_seller(user))


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local = timezone.localtime(now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    day_start, _ = _day_bounds(now)
    start = day_start.replace(day=1)
    return start, (start + timedelta(days=32)).replace(day=1)


def _window_bounds(
    window: str, package: UserPackage | None, now: datetime
) -> tuple[datetime, datetime | None]:
    if window == WindowKind.DAY:
        return _day_bounds(now)
    if window == WindowKind.PACKAGE and package is not None:
        return package.started_at, package.expires_at
    return _month_bounds(now)


def _allowance_text(tier: PackageTier) -> str:
    return f"{tier.quota_text}, {tier.devices_text}"


def _override_for(package: UserPackage) -> PackageQuotaOverride | None:
    return (
        package.quota_overrides.exclude(expires_at__lt=timezone.now())
        .order_by("-created_at")
        .first()
    )


def _pick(override_value: int | None, fallback: int) -> int:
    if override_value is None:
        return fallback
    return override_value if override_value > 0 else UNLIMITED


def _apply_override(
    current: Entitlements, override: PackageQuotaOverride
) -> Entitlements:
    changes_quota = (
        override.limit_files is not None or override.limit_size_mb is not None
    )
    limits = replace(
        current.limits,
        files=_pick(override.limit_files, current.limits.files),
        size_mb=_pick(override.limit_size_mb, current.limits.size_mb),
        metered=(
            frozenset(current.item_types)
            if changes_quota and not current.limits.metered
            else current.limits.metered
        ),
    )
    devices = (
        current.device_limit
        if override.device_limit is None
        else (override.device_limit or None)
    )
    label = (
        f"Staff override ({override.reason})" if override.reason else "Staff override"
    )
    return replace(
        current,
        limits=limits,
        download_quota=None if limits.files < 0 else limits.files,
        unlimited=limits.files < 0,
        device_limit=devices,
        sources=(*current.sources, label),
    )


def _user_override(user: Any) -> UserQuotaOverride | None:
    return (
        UserQuotaOverride.objects.filter(user=user, is_active=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .order_by("-created_at")
        .first()
    )


def _apply_user_override(
    current: Entitlements,
    override: UserQuotaOverride,
    package: UserPackage | None,
) -> Entitlements:
    """Account-level exception: wins over the package and its per-package override."""
    covers = {
        "free": override.covers_free,
        "featured": override.covers_featured,
        "paid": override.covers_paid,
    }
    item_types = tuple(
        name
        for name in ITEM_TYPES
        if (covers[name] if covers[name] is not None else name in current.item_types)
    )
    changes_quota = override.limit_files is not None or override.limit_size is not None
    window = override.window or current.window
    if override.window:
        window_start, resets_at = _window_bounds(window, package, timezone.now())
    else:
        window_start, resets_at = current.window_start, current.resets_at
    limits = replace(
        current.limits,
        window=window,
        files=_pick(override.limit_files, current.limits.files),
        size_mb=_pick(override.limit_size_mb, current.limits.size_mb),
        metered=(
            frozenset(item_types)
            if changes_quota or current.limits.metered
            else current.limits.metered
        ),
    )
    devices = (
        current.device_limit
        if override.device_limit is None
        else (override.device_limit or None)
    )
    return replace(
        current,
        limits=limits,
        window=window,
        window_start=window_start,
        resets_at=resets_at,
        download_quota=None if limits.files < 0 else limits.files,
        unlimited=limits.files < 0,
        device_limit=devices,
        item_types=item_types,
        can_access_featured=(
            current.can_access_featured
            if override.covers_featured is None
            else override.covers_featured
        ),
        sources=(*current.sources, f"Account override ({override.reason})"),
    )


def _resolve(user: Any) -> Entitlements:
    base = _free_package()
    package = _live_package(user) if _authenticated(user) else None
    label = "Free tier"
    if package is not None:
        base = package.package
        label = f"{base.name} package"
    limits = _limits(base)
    window_start, resets_at = _window_bounds(base.window, package, timezone.now())
    current = Entitlements(
        download_quota=None if limits.files < 0 else limits.files,
        window=base.window,
        unlimited=limits.files < 0,
        device_limit=base.device_limit if base.limit_devices else None,
        can_access_featured=base.applies_to_featured,
        can_upload_paid=False,
        package_name=base.name,
        sources=(f"{label}: {_allowance_text(base)}",),
        limits=limits,
        window_start=window_start,
        resets_at=resets_at,
        perks=Perks(
            ad_free=base.ad_free,
            priority_queue=base.priority_queue,
            resume_support=base.resume_support,
            direct_links=base.direct_links,
            early_access=base.early_access,
        ),
        item_types=base.item_types,
        paid_discount_percent=base.paid_discount_percent if base.applies_to_paid else 0,
        plan_summary=base.summary(),
        package=package,
        tier=base if base.pk else None,
        slot_reset_credits=base.slot_reset_credits,
        featured_blog=base.applies_to_featured_blog,
        featured_blog_limit=base.featured_blog_limit,
    )
    if not _authenticated(user):
        return current
    if package is not None:
        override = _override_for(package)
        if override is not None:
            current = _apply_override(current, override)
    account_override = _user_override(user)
    if account_override is not None:
        current = _apply_user_override(current, account_override, package)
    for modifier in _modifiers:
        current = modifier(user, current)
    return replace(current, can_upload_paid=_is_seller(user))


def effective_entitlements(user: Any) -> Entitlements:
    """The ONE resolver: free package + live package + staff override + modifiers.

    Memoised per request (see :mod:`apps.shop.request_cache`); outside a request it
    always resolves fresh.
    """
    key = user.pk if _authenticated(user) else None
    cached = request_cache.lookup(key)
    if cached is not None:
        return cached
    resolved = _resolve(user)
    request_cache.remember(key, resolved)
    return resolved


def register_entitlement_modifier(modifier: EntitlementModifier) -> None:
    """Plug in a ``(user, Entitlements) -> Entitlements`` step (e.g. campaign grants).

    Modifiers run in registration order after the staff override; registering the
    same callable twice is a no-op. Use :func:`grant` to add bonuses consistently.
    """
    if modifier not in _modifiers:
        _modifiers.append(modifier)
        request_cache.invalidate()


def unregister_entitlement_modifier(modifier: EntitlementModifier) -> None:
    if modifier in _modifiers:
        _modifiers.remove(modifier)
        request_cache.invalidate()


def grant(
    current: Entitlements,
    source: str,
    *,
    download_quota: int = 0,
    device_limit: int = 0,
    can_access_featured: bool | None = None,
    package_name: str | None = None,
) -> Entitlements:
    """Add a bonus to ``current`` and record where it came from.

    Bonuses never turn an unlimited dimension into a limited one.
    """
    limits = current.limits
    if download_quota and limits.files >= 0:
        limits = replace(limits, files=limits.files + download_quota)
    devices = current.device_limit
    if device_limit and devices is not None:
        devices += device_limit
    return replace(
        current,
        limits=limits,
        download_quota=None if limits.files < 0 else limits.files,
        unlimited=limits.files < 0,
        device_limit=devices,
        can_access_featured=(
            current.can_access_featured
            if can_access_featured is None
            else can_access_featured
        ),
        package_name=package_name or current.package_name,
        sources=(*current.sources, source),
    )


# --- Usage, rooms and meters ---------------------------------------------------


def _usage(user: Any, start: datetime, types: Any) -> tuple[int, int]:
    """(files, megabytes) consumed since ``start`` for the given item types."""
    if not _authenticated(user) or not types:
        return 0, 0
    totals = QuotaConsumption.objects.filter(
        user=user, consumed_at__gte=start, reason__in=types
    ).aggregate(files=Count("pk"), megabytes=Sum("size_mb"))
    return totals["files"], totals["megabytes"] or 0


def _room(limit: int, extra: int, used_window: int, used_today: int) -> int | None:
    """New units still allowed; ``None`` = unlimited.

    The window allowance is spent first; the extra daily allowance is added on top
    for today, so a user who exhausted the window still gets ``extra`` each day.
    """
    if limit < 0:
        return None
    capacity_today = max(0, limit - (used_window - used_today)) + extra
    return max(0, capacity_today - used_today)


@dataclass(frozen=True)
class _MainUsage:
    files_used: int
    mb_used: int
    files_room: int | None
    mb_room: int | None


def _main_usage(user: Any, entitled: Entitlements) -> _MainUsage:
    limits = entitled.limits
    now = timezone.now()
    day_start = max(_day_bounds(now)[0], entitled.window_start)
    files, megabytes = _usage(user, entitled.window_start, limits.metered)
    today_files, today_mb = _usage(user, day_start, limits.metered)
    return _MainUsage(
        files_used=files,
        mb_used=megabytes,
        files_room=_room(limits.files, limits.extra_daily_files, files, today_files),
        mb_room=_room(limits.size_mb, limits.extra_daily_size_mb, megabytes, today_mb),
    )


def _snapshot(
    *,
    kind: str,
    title: str,
    window_label: str,
    resets_at: datetime | None,
    limit: int,
    used: int,
    remaining: int | None,
    package_name: str,
) -> QuotaSnapshot:
    unlimited = limit < 0
    return QuotaSnapshot(
        limit=None if unlimited else limit,
        used=used,
        remaining=None if unlimited else remaining,
        window_label=window_label,
        resets_at=resets_at,
        package_name=package_name,
        unlimited=unlimited,
        kind=kind,
        title=title,
    )


def quota_windows(user: Any) -> list[QuotaSnapshot]:
    """Every active meter: the package allowance, then fair-usage caps.

    When nothing is limited a single unlimited files meter is returned.
    """
    entitled = effective_entitlements(user)
    limits = entitled.limits
    name = entitled.package_name
    now = timezone.now()
    main = _main_usage(user, entitled)
    label = WINDOW_LABELS.get(entitled.window, entitled.window)
    meters: list[QuotaSnapshot] = []
    if limits.files >= 0:
        meters.append(
            _snapshot(
                kind="files",
                title="",
                window_label=label,
                resets_at=entitled.resets_at,
                limit=limits.files,
                used=main.files_used,
                remaining=main.files_room,
                package_name=name,
            )
        )
    if limits.size_mb >= 0:
        meters.append(
            _snapshot(
                kind="size",
                title="",
                window_label=label,
                resets_at=entitled.resets_at,
                limit=limits.size_mb,
                used=main.mb_used,
                remaining=main.mb_room,
                package_name=name,
            )
        )
    day_start, day_end = _day_bounds(now)
    month_start, month_end = _month_bounds(now)
    day_files, day_mb = _usage(user, day_start, FAIR_USAGE_TYPES)
    month_files, month_mb = _usage(user, month_start, FAIR_USAGE_TYPES)
    fair = (
        ("files", "today", day_end, limits.fair_daily_files, day_files),
        ("size", "today", day_end, limits.fair_daily_mb, day_mb),
        ("files", "this month", month_end, limits.fair_monthly_files, month_files),
        ("size", "this month", month_end, limits.fair_monthly_mb, month_mb),
    )
    for kind, window_label, resets_at, cap, used in fair:
        if cap < 0:
            continue
        noun = "files" if kind == "files" else "size"
        meters.append(
            _snapshot(
                kind=kind,
                title=f"Fair use · {noun} {window_label}",
                window_label=window_label,
                resets_at=resets_at,
                limit=cap,
                used=used,
                remaining=max(0, cap - used),
                package_name=name,
            )
        )
    if not meters:
        meters.append(
            _snapshot(
                kind="files",
                title="",
                window_label=label,
                resets_at=entitled.resets_at,
                limit=UNLIMITED,
                used=main.files_used,
                remaining=None,
                package_name=name,
            )
        )
    return meters


def quota_snapshot(user: Any) -> QuotaSnapshot:
    """The binding file allowance: the files meter with the least room left.

    Unlimited meters never bind; when every file meter is unlimited the first is shown.
    """
    files = [meter for meter in quota_windows(user) if meter.kind == "files"]
    limited = [meter for meter in files if not meter.unlimited]
    if not limited:
        return files[0]
    return min(limited, key=lambda meter: meter.remaining or 0)


def _check_limits(
    user: Any, entitled: Entitlements, item_type: str, size_mb: int
) -> QuotaDecision:
    """Whether a new free or featured file of ``size_mb`` fits every cap."""
    limits = entitled.limits
    now = timezone.now()
    if limits.cooldown_seconds:
        last = (
            QuotaConsumption.objects.filter(user=user, reason__in=FAIR_USAGE_TYPES)
            .order_by("-consumed_at")
            .values_list("consumed_at", flat=True)
            .first()
        )
        if last is not None:
            wait = limits.cooldown_seconds - (now - last).total_seconds()
            if wait > 0:
                return QuotaDecision(
                    False,
                    f"Wait {math.ceil(wait)} seconds before your next new file.",
                )
    label = WINDOW_LABELS.get(entitled.window, entitled.window)
    if item_type in limits.metered:
        main = _main_usage(user, entitled)
        if main.files_room is not None and main.files_room < 1:
            return QuotaDecision(False, f"File allowance used up {label}.")
        if main.mb_room is not None and main.mb_room < size_mb:
            return QuotaDecision(False, f"Size allowance used up {label}.")
    day_start, _ = _day_bounds(now)
    month_start, _ = _month_bounds(now)
    day_files, day_mb = _usage(user, day_start, FAIR_USAGE_TYPES)
    month_files, month_mb = _usage(user, month_start, FAIR_USAGE_TYPES)
    caps = (
        (limits.fair_daily_files, day_files, 1, "Fair-usage file limit reached today."),
        (
            limits.fair_monthly_files,
            month_files,
            1,
            "Fair-usage file limit reached this month.",
        ),
        (limits.fair_daily_mb, day_mb, size_mb, "Fair-usage size limit reached today."),
        (
            limits.fair_monthly_mb,
            month_mb,
            size_mb,
            "Fair-usage size limit reached this month.",
        ),
    )
    for cap, used, increment, message in caps:
        if cap >= 0 and used + increment > cap:
            return QuotaDecision(False, message)
    return QuotaDecision(True)


def _current_period_id(user: Any) -> int | None:
    """Pk of the package period the user is in now (``None`` = no live package)."""
    package = effective_entitlements(user).package
    return package.pk if package is not None else None


def _consumption(
    user: Any, content_type: ContentType, object_id: Any
) -> QuotaConsumption | None:
    return QuotaConsumption.objects.filter(
        user=user, content_type=content_type, object_id=object_id
    ).first()


def _is_current(user: Any, row: QuotaConsumption | None) -> bool:
    """Whether ``row`` still covers its file.

    A featured file counted in an earlier package period no longer does: serving it
    under the current period counts it again, which keeps the new window honest.
    """
    if row is None:
        return False
    if row.reason != QuotaConsumption.Reason.FEATURED:
        return True
    return row.user_package_id == _current_period_id(user)


def _already_consumed(user: Any, content_type: ContentType, object_id: Any) -> bool:
    return _is_current(user, _consumption(user, content_type, object_id))


def check_quota(user: Any, firmware: Any) -> QuotaDecision:
    """Read-only: may this file be served without exceeding the allowance?

    Files already served and paid files are always allowed; a new free or featured
    file has to fit the bucket of its item type and the fair-usage caps.
    """
    if not _authenticated(user):
        return QuotaDecision(False, "Sign in to download this file.")
    content_type, object_id = file_ref(firmware)
    item_type = _access_tier(firmware)
    if (
        item_type == QuotaConsumption.Reason.PAID
        or _already_consumed(user, content_type, object_id)
        or (
            item_type == QuotaConsumption.Reason.FEATURED
            and has_purchased(user, firmware)
        )
    ):
        return QuotaDecision(True)
    entitled = effective_entitlements(user)
    if (
        item_type == QuotaConsumption.Reason.FEATURED
        and not entitled.can_access_featured
    ):
        return QuotaDecision(False, "Featured files need a package that includes them.")
    return _check_limits(user, entitled, item_type, _size_mb(firmware))


@transaction.atomic
def consume_quota(user: Any, firmware: Any, *, reason: str) -> None:
    """Count ``firmware`` against the user's allowance, at most once per file.

    ``reason`` must equal the file's item type. Paid files require a purchase,
    featured files a package that covers them; free and featured files must fit the
    allowance (:class:`QuotaExceeded`). A second call for the same file is a no-op.
    """
    if not _authenticated(user):
        raise NotEntitled("Sign in to download this file.")
    if reason not in QuotaConsumption.Reason.values:
        raise ValueError(f"Unknown quota reason: {reason!r}")
    if reason != _access_tier(firmware):
        raise ValueError(f"Reason {reason!r} does not match the file's access tier.")

    lock_user(user)
    content_type, object_id = file_ref(firmware)
    existing = _consumption(user, content_type, object_id)
    if _is_current(user, existing):
        return
    entitled = effective_entitlements(user)
    if reason == QuotaConsumption.Reason.PAID and not has_purchased(user, firmware):
        raise NotEntitled("This file has not been purchased.")
    if reason == QuotaConsumption.Reason.FEATURED and has_purchased(user, firmware):
        # Bought on its own: recorded as served, never counted in the allowance.
        reason = QuotaConsumption.Reason.PURCHASED
    elif (
        reason == QuotaConsumption.Reason.FEATURED and not entitled.can_access_featured
    ):
        raise NotEntitled("Featured files need a package that includes them.")
    if reason in FAIR_USAGE_TYPES:
        decision = _check_limits(user, entitled, reason, _size_mb(firmware))
        if not decision.allowed:
            raise QuotaExceeded(decision.reason)
    period = entitled.package
    if existing is not None:
        existing.reason = reason
        existing.size_mb = _size_mb(firmware)
        existing.consumed_at = timezone.now()
        existing.user_package = period
        existing.save(
            update_fields=["reason", "size_mb", "consumed_at", "user_package"]
        )
        return
    try:
        with transaction.atomic():
            QuotaConsumption.objects.create(
                user=user,
                content_type=content_type,
                object_id=object_id,
                reason=reason,
                size_mb=_size_mb(firmware),
                user_package=period,
            )
    except IntegrityError:
        # A concurrent request charged this file first; the unique constraint won.
        return


# --- Read models for pages ---------------------------------------------------


def _type_key(model_class: Any) -> str | None:
    """The firmware type key (``official``…) for a typed firmware model class."""
    if model_class is None or not django_apps.is_installed("apps.firmwares"):
        return None
    from apps.firmwares.catalog import TYPE_KEY_BY_CLASS

    return TYPE_KEY_BY_CLASS.get(model_class)


def file_links(content_type_id: int, object_id: Any) -> tuple[str, str] | None:
    """(detail URL, download-gate URL) for a typed firmware row, if it can be linked."""
    model_class = ContentType.objects.get_for_id(content_type_id).model_class()
    type_key = _type_key(model_class)
    if type_key is None:
        return None
    kwargs = {"firmware_type": type_key, "firmware_id": object_id}
    return (
        reverse("firmwares:firmware_detail", kwargs=kwargs),
        reverse("firmwares:firmware_download", kwargs=kwargs),
    )


def purchase_url(firmware: Any) -> str:
    """POST target that requests the purchase of one paid file."""
    type_key = _type_key(type(firmware))
    if type_key is None:
        return ""
    return reverse(
        "shop:request_file",
        kwargs={"firmware_type": type_key, "firmware_id": firmware.pk},
    )


def credits_url(firmware: Any) -> str:
    """Confirm-and-pay page that buys one paid file with credits."""
    type_key = _type_key(type(firmware))
    if type_key is None:
        return ""
    return reverse(
        "shop:file_credits",
        kwargs={"firmware_type": type_key, "firmware_id": firmware.pk},
    )


@dataclass(frozen=True)
class PurchasedFile:
    """A paid file the user owns but has not downloaded yet."""

    label: str
    price: Decimal
    currency: str
    paid_at: datetime | None
    detail_url: str
    download_url: str


def purchased_unserved(user: Any) -> list[PurchasedFile]:
    """Paid, not refunded files that have never been served to the user."""
    if not _authenticated(user):
        return []
    served = set(
        QuotaConsumption.objects.filter(user=user).values_list(
            "content_type_id", "object_id"
        )
    )
    items = OrderItem.objects.filter(
        order__user=user,
        order__status=Order.Status.PAID,
        content_type__isnull=False,
    ).select_related("order")
    seen: set[tuple[int | None, Any]] = set()
    files: list[PurchasedFile] = []
    for item in items.order_by("-order__paid_at", "-pk"):
        key = (item.content_type_id, item.object_id)
        if item.content_type_id is None or key in served or key in seen:
            continue
        seen.add(key)
        links = file_links(item.content_type_id, item.object_id)
        files.append(
            PurchasedFile(
                label=item.description,
                price=item.line_total,
                currency=item.order.currency,
                paid_at=item.order.paid_at,
                detail_url=links[0] if links else "",
                download_url=links[1] if links else "",
            )
        )
    return files


@dataclass(frozen=True)
class EntitlementSummary:
    """Read-only view of what an account may download; never writes."""

    package_name: str
    tier: PackageTier | None
    package: UserPackage | None
    windows: list[QuotaSnapshot]
    sources: tuple[str, ...]
    plan_summary: str
    perks: list[str]
    devices_text: str

    @property
    def is_default_allowance(self) -> bool:
        return self.package is None


_PERK_LABELS = (
    ("ad_free", "Ad-free downloads"),
    ("priority_queue", "Priority queue"),
    ("resume_support", "Resumable downloads"),
    ("direct_links", "Direct links"),
    ("early_access", "Early access"),
)


def entitlement_summary(user: Any) -> EntitlementSummary:
    """Allowance meters and perks for the account's live or default package."""
    entitled = effective_entitlements(user)
    return EntitlementSummary(
        package_name=entitled.package_name,
        tier=entitled.tier,
        package=entitled.package,
        windows=quota_windows(user),
        sources=entitled.sources,
        plan_summary=entitled.plan_summary,
        perks=[label for attr, label in _PERK_LABELS if getattr(entitled.perks, attr)],
        devices_text=entitled.devices_text,
    )
