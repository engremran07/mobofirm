"""Shop business logic: offline order fulfilment and packages.

Per-file entitlements and download quotas live in :mod:`apps.shop.entitlements`.

Online payments are intentionally not implemented. ``ONLINE_PAYMENTS_ENABLED`` is a
fixed ``False`` so every UI and API can show the disabled state; enabling a payment
provider is a separate, owner-approved change.

Paying or refunding an order publishes ``shop.order_paid`` / ``shop.order_refunded``
on the event bus after commit, with ``{"order_id", "user_id", "files": [{"content_type":
"app_label.model", "object_id": "<uuid>", "amount": "9.99"}]}`` (paid-file lines only).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core.events.bus import event_bus

from . import promotion_gateway, request_cache, wallet_gateway
from .models import LIFETIME, Order, OrderItem, PackageTier, UserPackage

ONLINE_PAYMENTS_ENABLED = False
ORDER_PAID_EVENT = "shop.order_paid"
ORDER_REFUNDED_EVENT = "shop.order_refunded"


class OrderError(ValueError):
    """Raised when an order cannot be created or transitioned."""


def lock_user(user: Any) -> None:
    """Serialise one user's order/quota writes by locking only the user row.

    ``of=("self",)`` keeps PostgreSQL from locking joined rows; SQLite ignores it.
    """
    get_user_model().objects.select_for_update(of=("self",)).get(pk=user.pk)


def _expiry(start: datetime, duration_days: int) -> datetime | None:
    return None if duration_days == LIFETIME else start + timedelta(days=duration_days)


def _live_packages(user: Any) -> Any:
    """Purchased or granted active packages (the free baseline row is never expired)."""
    return UserPackage.objects.filter(
        user=user, status=UserPackage.Status.ACTIVE, is_baseline=False
    )


@transaction.atomic
def create_order(user: Any, package: PackageTier) -> Order:
    """Create a pending offline order for one public, paid package."""
    if not (package.is_active and package.is_public) or package.is_default:
        raise OrderError("This package is not available.")
    if package.price <= 0:
        raise OrderError("This package is free; it does not need an order.")
    lock_user(user)
    duplicate = Order.objects.filter(
        user=user, status=Order.Status.PENDING, items__package=package
    ).first()
    if duplicate is not None:
        return duplicate
    quote = promotion_gateway.quote(user, package)
    price = quote.price if quote is not None else package.price
    order = Order.objects.create(user=user, total=price)
    OrderItem.objects.create(
        order=order,
        package=package,
        description=package.name,
        duration_days=package.duration_days,
        unit_price=price,
        line_total=price,
    )
    if quote is not None:
        promotion_gateway.redeem(user, package, reference=str(order.pk))
    return order


@transaction.atomic
def activate_package(
    user: Any,
    package: PackageTier,
    *,
    reference: str = "",
    order: Order | None = None,
    source: str = UserPackage.Source.ORDER,
    duration_days: int | None = None,
) -> UserPackage:
    """Make ``package`` the user's one live package, ending any previous one.

    Renewing the package the user already holds adds the new period after the
    current end date instead of overlapping it. ``duration_days`` defaults to the
    package's own duration (0 = lifetime).
    """
    lock_user(user)
    now = timezone.now()
    days = package.duration_days if duration_days is None else duration_days
    start = now
    previous = list(_live_packages(user))
    for held in previous:
        if (
            held.package_id == package.pk
            and held.expires_at is not None
            and held.expires_at > now
        ):
            start = held.expires_at
    _live_packages(user).update(status=UserPackage.Status.EXPIRED)
    request_cache.invalidate()
    return UserPackage.objects.create(
        user=user,
        package=package,
        status=UserPackage.Status.ACTIVE,
        source=source,
        started_at=now,
        expires_at=_expiry(start, days),
        payment_reference=reference,
        order=order,
    )


@transaction.atomic
def cancel_package(user_package: UserPackage) -> UserPackage:
    if user_package.is_baseline:
        raise OrderError("The free package cannot be cancelled.")
    user_package.status = UserPackage.Status.CANCELLED
    user_package.cancelled_at = timezone.now()
    user_package.save(update_fields=["status", "cancelled_at"])
    request_cache.invalidate()
    return user_package


def _file_lines(order: Order) -> list[dict[str, Any]]:
    """File lines of an order, with the credits paid split across them.

    Credits are divided in proportion to each line's price; the rounding remainder
    goes to the last line so the shares always add up to the credits actually spent.
    """
    total_credits = order.credits_spent if order.payment_method == "credits" else 0
    items = [
        item
        for item in order.items.select_related("content_type")
        if item.content_type is not None
    ]
    total_price = sum((item.line_total for item in items), Decimal(0))
    lines: list[dict[str, Any]] = []
    assigned = 0
    for index, item in enumerate(items):
        if index == len(items) - 1:
            share = total_credits - assigned
        elif total_price > 0:
            share = int(total_credits * item.line_total / total_price)
        else:
            share = total_credits // len(items)
        assigned += share
        content_type = item.content_type
        assert content_type is not None
        lines.append(
            {
                "content_type": f"{content_type.app_label}.{content_type.model}",
                "object_id": str(item.object_id),
                "amount": f"{item.line_total:.2f}",
                "credits": share,
            }
        )
    return lines


def _publish_after_commit(event: str, order: Order) -> None:
    payload = {
        "order_id": order.pk,
        "user_id": order.user_id,
        "files": _file_lines(order),
        "topup_credits": sum(item.topup_credits for item in order.items.all()),
    }
    transaction.on_commit(lambda: event_bus.publish(event, payload))


def _provision_item(order: Order, item: OrderItem) -> None:
    """Provision a package line; a paid file line needs no records (the order is)."""
    if item.package is None:
        return
    activate_package(
        order.user,
        item.package,
        reference=order.payment_reference,
        order=order,
        source=UserPackage.Source.ORDER,
        duration_days=item.duration_days,
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
    for item in order.items.select_related("package"):
        _provision_item(order, item)
    _publish_after_commit(ORDER_PAID_EVENT, order)
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


@transaction.atomic
def refund_order(order_id: int, *, staff_user: Any, reason: str = "") -> Order:
    """Refund a paid order (money is returned offline) and revoke what it granted.

    Paid-file lines stop being purchased the moment the order leaves ``paid``;
    packages provisioned by the order are cancelled. Files that were already
    served stay in the user's library but can no longer be re-downloaded.
    """
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.Status.REFUNDED:
        return order
    if order.status != Order.Status.PAID:
        raise OrderError("Only paid orders can be refunded.")
    now = timezone.now()
    order.status = Order.Status.REFUNDED
    order.processed_by = staff_user
    if reason:
        order.notes = f"{order.notes}\nRefund: {reason}".strip()
    order.save(update_fields=["status", "processed_by", "notes", "updated_at"])
    if order.payment_method == Order.PaymentMethod.CREDITS and order.credits_spent:
        try:
            wallet_gateway.refund(
                order.payment_reference, reason=reason or f"Order #{order.pk} refunded"
            )
        except wallet_gateway.CreditsUnavailable as error:
            raise OrderError(str(error)) from error
    UserPackage.objects.filter(order=order, status=UserPackage.Status.ACTIVE).update(
        status=UserPackage.Status.CANCELLED, cancelled_at=now
    )
    request_cache.invalidate()
    _publish_after_commit(ORDER_REFUNDED_EVENT, order)
    return order


def expire_overdue_packages() -> dict[str, int]:
    """Expire packages whose end date passed. Idempotent."""
    packages = UserPackage.objects.filter(
        status=UserPackage.Status.ACTIVE, expires_at__lt=timezone.now()
    ).update(status=UserPackage.Status.EXPIRED)
    request_cache.invalidate()
    return {"packages": packages}


# --- Package administration -------------------------------------------------


def set_package_active(package: PackageTier, *, active: bool) -> PackageTier:
    """Activate or deactivate a package; holders keep it, nobody can order it."""
    if not active and package.is_default:
        raise OrderError("The default package must stay active.")
    package.is_active = active
    package.save(update_fields=["is_active", "updated_at"])
    request_cache.invalidate()
    return package


@transaction.atomic
def archive_package(package: PackageTier) -> PackageTier:
    """Retire a package: hide it, stop orders and cancel its pending orders.

    Current holders keep their package until it ends. The default package cannot
    be archived.
    """
    if package.is_default:
        raise OrderError("The default package cannot be archived.")
    pending = Order.objects.select_for_update().filter(
        status=Order.Status.PENDING, items__package=package
    )
    pending.update(status=Order.Status.CANCELLED)
    package.is_active = False
    package.is_public = False
    package.save(update_fields=["is_active", "is_public", "updated_at"])
    request_cache.invalidate()
    return package


@transaction.atomic
def duplicate_package(package: PackageTier) -> PackageTier:
    """Copy a package as an inactive, hidden, non-default draft."""
    base_name = f"Copy of {package.name}"[:90]
    name, number = base_name, 1
    while PackageTier.objects.filter(name=name).exists():
        number += 1
        name = f"{base_name} {number}"
    slug_base = slugify(name)[:90] or "package"
    slug, number = slug_base, 1
    while PackageTier.objects.filter(slug=slug).exists():
        number += 1
        slug = f"{slug_base}-{number}"
    values = {
        field.name: getattr(package, field.name)
        for field in PackageTier._meta.concrete_fields
        if field.name not in {"id", "created_at", "updated_at"}
    }
    values.update(
        name=name, slug=slug, is_default=False, is_active=False, is_public=False
    )
    return PackageTier.objects.create(**values)
