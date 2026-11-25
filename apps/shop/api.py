"""Public service contract of the shop for other apps."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from .blog_access import (
    BlogAccess,
    featured_blog_access,
    featured_blog_buyers,
    purchase_featured_blog,
    purchased_featured_blog_ids,
)
from .models import Order, OrderItem

__all__ = [
    "BlogAccess",
    "cancel_credit_topup_order",
    "confirm_credit_topup_order",
    "create_credit_topup_order",
    "featured_blog_access",
    "featured_blog_buyers",
    "purchase_featured_blog",
    "purchased_featured_blog_ids",
]


@transaction.atomic
def create_credit_topup_order(
    user: Any, *, credit_amount: int, price: Decimal, reference: str = ""
) -> int:
    """Open an offline order for a credit top-up; staff mark it paid.

    Paying the order publishes ``shop.order_paid`` with ``topup_credits`` so the
    wallet can credit the account. Returns the order id.
    """
    if credit_amount <= 0:
        raise ValueError("A top-up needs a positive number of credits.")
    if price <= 0:
        raise ValueError("A top-up needs a positive price.")
    order = Order.objects.create(
        user=user, total=price, payment_reference=reference[:200]
    )
    OrderItem.objects.create(
        order=order,
        description=f"{credit_amount} credits",
        topup_credits=credit_amount,
        unit_price=price,
        line_total=price,
    )
    return order.pk


def cancel_credit_topup_order(
    order_id: int,
    *,
    topup_id: int,
    user_id: int,
    credit_amount: int,
    processed_by: Any | None = None,
) -> Order:
    """Cancel the exact shop order linked to a wallet top-up."""
    from .services import cancel_credit_topup_order as cancel_order

    return cancel_order(
        order_id,
        topup_id=topup_id,
        user_id=user_id,
        credit_amount=credit_amount,
        processed_by=processed_by,
    )


def confirm_credit_topup_order(
    order_id: int,
    *,
    topup_id: int,
    user_id: int,
    credit_amount: int,
    staff_user: Any | None,
    reference: str = "",
) -> Order:
    """Synchronize wallet staff confirmation with the linked shop order."""
    from .services import confirm_credit_topup_order as confirm_order

    return confirm_order(
        order_id,
        topup_id=topup_id,
        user_id=user_id,
        credit_amount=credit_amount,
        staff_user=staff_user,
        reference=reference,
    )
