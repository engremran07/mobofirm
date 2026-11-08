"""Template tags other apps use to show the credit balance."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled
from apps.wallet import services

register = template.Library()


@register.filter
def price_with_credits(amount: Any) -> str:
    """``$4.00 · 40 credits`` for a currency amount (empty for ``None``)."""
    if amount in (None, ""):
        return ""
    return services.price_label(Decimal(str(amount)))


@register.inclusion_tag("wallet/_balance_chip.html", takes_context=True)
def wallet_balance_chip(context: template.Context) -> dict[str, Any]:
    """Compact "N credits" link for signed-in users; renders nothing otherwise."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not app_enabled("wallet"):
        return {"balance": None}
    return {"balance": services.balance(user), "url": reverse("wallet:dashboard")}
