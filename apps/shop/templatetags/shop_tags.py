"""Template seams that let other apps show shop information without importing it."""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled

from .. import services
from ..models import Order

register = template.Library()


@register.inclusion_tag("components/dashboard_tile.html", takes_context=True)
def shop_dashboard_tile(context: dict[str, Any]) -> dict[str, Any]:
    """Account dashboard tile: current package and open orders."""
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("shop"):
        return {}
    summary = services.entitlement_summary(user)
    pending = Order.objects.filter(user=user, status=Order.Status.PENDING).count()
    hint = (
        f"{pending} order(s) awaiting payment confirmation"
        if pending
        else "Orders and allowances"
    )
    return {
        "url": reverse("shop:my_subscriptions"),
        "label": "Download package",
        "value": summary.tier.name if summary.tier else "No package",
        "icon": "package",
        "hint": hint,
    }


@register.inclusion_tag("shop/_firmware_allowance.html", takes_context=True)
def shop_firmware_allowance(
    context: dict[str, Any], firmware_type: str
) -> dict[str, Any]:
    """Allowance note for signed-in visitors on a firmware page (empty otherwise)."""
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("shop"):
        return {}
    summary = services.entitlement_summary(user)
    if summary.tier is None:
        return {}
    gated = firmware_type in services.TIER_GATED_TYPES
    allowed = {label.lower(): ok for label, ok in summary.type_access}.get(
        firmware_type, True
    )
    return {
        "summary": summary,
        "type_allowed": allowed if gated else True,
        "daily": summary.meters[0] if summary.meters else None,
        "pricing_url": reverse("shop:pricing"),
        "account_url": reverse("shop:my_subscriptions"),
    }
