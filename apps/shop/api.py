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
