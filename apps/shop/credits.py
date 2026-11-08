"""Pay for packages and paid files with wallet credits.

Credits are spent through ``apps.wallet`` (idempotent on a ``shop:<kind>:<user>:
<object>:<n>`` key) and fulfil instantly: the purchase is recorded as an order paid by
credits, so the same entitlement rules apply as for staff-confirmed cash orders.
Staff refunds of such an order reverse the spend in the wallet.
"""

from __future__ import annotations

import math
from typing import Any

from django.db import transaction

from . import entitlements, promotion_gateway, services, wallet_gateway
from .models import Order, OrderItem, PackageTier, QuotaConsumption
from .spend_sinks import FILE_PURCHASE, PACKAGE_PURCHASE
from .wallet_gateway import (
    CreditsUnavailable,
    InsufficientCreditsError,
)

__all__ = [
    "CreditsUnavailable",
    "InsufficientCreditsError",
    "credits_for_file",
    "credits_for_package",
    "pay_file_with_credits",
    "pay_package_with_credits",
    "purchase_block_reason",
]


def credits_for_package(package: PackageTier, user: Any = None) -> int:
    """The package's credit price (0 = not purchasable with credits).

    A live promotional credit price for ``user`` replaces the regular one.
    """
    if user is not None and package.credit_price:
        quote = promotion_gateway.quote(user, package)
        if quote is not None:
            return int(quote.credit_price)
    return package.credit_price


def credits_for_file(user: Any, firmware: Any) -> int:
    """Credits for one file: its explicit ``credit_price``, else its price converted.

    The package's paid-file discount applies to paid files. 0 means credits cannot be
    used (no wallet, no approved price, or a featured file the package already covers).
    """
    if not wallet_gateway.available() or purchase_block_reason(user, firmware):
        return 0
    tier = entitlements.file_access_tier(firmware)
    discount = (
        entitlements.effective_entitlements(user).paid_discount_percent
        if tier == QuotaConsumption.Reason.PAID
        else 0
    )
    explicit = getattr(firmware, "credit_price", None)
    if explicit:
        return max(1, math.ceil(int(explicit) * (100 - discount) / 100))
    price = entitlements.price_for(user, firmware)
    if price is None:
        return 0
    return wallet_gateway.to_credits(price)


def purchase_block_reason(user: Any, firmware: Any) -> str:
    """Why this file cannot be bought with credits right now ('' when it can).

    Paid files are always bought one by one. Featured files are bought one by one only
    by accounts whose package does not already include them: package holders use the
    package quota instead, so they are never charged twice.
    """
    tier = entitlements.file_access_tier(firmware)
    if tier not in (QuotaConsumption.Reason.PAID, QuotaConsumption.Reason.FEATURED):
        return "This file is not sold; it does not need a purchase."
    if entitlements.price_for(user, firmware) is None:
        if tier == QuotaConsumption.Reason.FEATURED:
            return "Featured files cannot be bought one by one right now."
        return "This file has no approved price yet, so it cannot be bought."
    if not (
        getattr(firmware, "is_active", False)
        and getattr(firmware, "is_verified", False)
    ):
        return "This file is not available."
    if getattr(firmware, "uploader_id", None) == user.pk:
        return "You uploaded this file; it is already yours."
    if (
        tier == QuotaConsumption.Reason.FEATURED
        and entitlements.effective_entitlements(user).can_access_featured
    ):
        return "Your package already includes featured files; no purchase is needed."
    return ""


def _next_key(user: Any, kind: str, object_id: Any) -> str:
    taken = Order.objects.filter(
        user=user,
        payment_method=Order.PaymentMethod.CREDITS,
        payment_reference__startswith=f"shop:{kind}:{user.pk}:{object_id}:",
    ).count()
    return f"shop:{kind}:{user.pk}:{object_id}:{taken + 1}"


def _charge(user: Any, sink: str, key: str, order: Order, amount: int) -> None:
    wallet_gateway.spend(
        user, sink, key=key, reference=f"order:{order.pk}", amount=amount
    )
    services.mark_order_paid(order.pk, staff_user=None, reference=key)


@transaction.atomic
def pay_package_with_credits(user: Any, package: PackageTier) -> Order:
    """Buy ``package`` with credits and activate it immediately."""
    cost = credits_for_package(package, user)
    if cost <= 0:
        raise entitlements.PurchaseError("This package cannot be bought with credits.")
    if not (package.is_active and package.is_public) or package.is_default:
        raise entitlements.PurchaseError("This package is not available.")
    services.lock_user(user)
    key = _next_key(user, "package", package.pk)
    quote = promotion_gateway.quote(user, package)
    price = quote.price if quote is not None else package.price
    order = Order.objects.create(
        user=user,
        total=price,
        payment_method=Order.PaymentMethod.CREDITS,
        payment_reference=key,
        credits_spent=cost,
    )
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
    _charge(user, PACKAGE_PURCHASE, key, order, cost)
    order.refresh_from_db()
    return order


@transaction.atomic
def pay_file_with_credits(user: Any, firmware: Any) -> Order:
    """Buy one paid or featured file with credits; the download unlocks immediately.

    Atomic: the spend, the order and the entitlement commit together, so a failure
    after the charge rolls the charge back. The entitlement is per file and lifetime:
    it bypasses the free quota and the item-type restriction for this file only.
    """
    reason = purchase_block_reason(user, firmware)
    if reason:
        raise entitlements.PurchaseError(reason)
    amount = entitlements.price_for(user, firmware)
    if amount is None:
        raise entitlements.PurchaseError(
            "This file has no approved price yet, so it cannot be bought."
        )
    services.lock_user(user)
    if entitlements.has_purchased(user, firmware):
        raise entitlements.PurchaseError("You already own this file.")
    cost = credits_for_file(user, firmware)
    if cost <= 0:
        raise entitlements.PurchaseError("This file cannot be bought with credits.")
    content_type, object_id = entitlements.file_ref(firmware)
    key = _next_key(user, "file", object_id)
    order = Order.objects.create(
        user=user,
        total=amount,
        payment_method=Order.PaymentMethod.CREDITS,
        payment_reference=key,
        credits_spent=cost,
    )
    OrderItem.objects.create(
        order=order,
        content_type=content_type,
        object_id=object_id,
        description=str(getattr(firmware, "original_file_name", ""))[:255],
        unit_price=amount,
        line_total=amount,
    )
    Order.objects.filter(
        user=user,
        status=Order.Status.PENDING,
        items__content_type=content_type,
        items__object_id=object_id,
    ).exclude(pk=order.pk).update(status=Order.Status.CANCELLED)
    _charge(user, FILE_PURCHASE, key, order, cost)
    order.refresh_from_db()
    return order
