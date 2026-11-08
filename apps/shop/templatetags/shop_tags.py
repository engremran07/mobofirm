"""Template seams that let other apps show shop information without importing it."""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled

from .. import (
    credits as credit_purchase,
)
from .. import (
    entitlements,
    promotion_gateway,
    wallet_gateway,
)
from ..models import Order, OrderItem, format_money

register = template.Library()


@register.inclusion_tag("components/dashboard_tile.html", takes_context=True)
def shop_dashboard_tile(context: dict[str, Any]) -> dict[str, Any]:
    """Account dashboard tile: current package, remaining allowance and open orders."""
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("shop"):
        return {}
    snapshot = entitlements.quota_snapshot(user)
    pending = Order.objects.filter(user=user, status=Order.Status.PENDING).count()
    if pending:
        hint = f"{pending} order(s) awaiting payment confirmation"
    elif snapshot.remaining is None:
        hint = "Unlimited new files"
    else:
        hint = f"{snapshot.remaining} new files left {snapshot.window_label}"
    return {
        "url": reverse("shop:my_package"),
        "label": "Download package",
        "value": snapshot.package_name,
        "icon": "package",
        "hint": hint,
    }


@register.inclusion_tag("shop/_firmware_allowance.html", takes_context=True)
def shop_firmware_allowance(context: dict[str, Any], firmware: Any) -> dict[str, Any]:
    """Allowance note for signed-in visitors on a firmware page (empty otherwise).

    Says how this file is covered: free, bought, or needing a purchase/package.
    """
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("shop"):
        return {}
    tier = str(getattr(firmware, "access_tier", "") or "free")
    sold = tier in ("paid", "featured")
    owned = sold and entitlements.has_purchased(user, firmware)
    entitled = entitlements.effective_entitlements(user)
    can_request = (
        tier == "paid"
        and not owned
        and getattr(firmware, "uploader_id", None) != user.pk
    )
    credit_cost = (
        credit_purchase.credits_for_file(user, firmware) if sold and not owned else 0
    )
    file_price = entitlements.price_for(user, firmware) if sold else None
    return {
        "snapshot": entitlements.quota_snapshot(user),
        "plan": entitled.plan_summary,
        "access_tier": tier,
        "price": file_price if tier == "paid" else None,
        "list_price": getattr(firmware, "approved_price", None),
        "owned": owned,
        "can_request": can_request,
        "request_url": entitlements.purchase_url(firmware) if tier == "paid" else "",
        "credits": credit_cost,
        "credits_url": entitlements.credits_url(firmware) if credit_cost else "",
        "price_label": (
            wallet_gateway.price_label(file_price)
            if credit_cost and file_price is not None
            else ""
        ),
        "has_package": tier == "featured" and entitled.can_access_featured,
        "pricing_url": reverse("shop:pricing"),
        "account_url": reverse("shop:my_package"),
        "files_url": reverse("library:my_files"),
    }


@register.simple_tag(takes_context=True)
def package_promo(context: dict[str, Any], package: Any) -> dict[str, Any] | None:
    """The promotional price of ``package`` for this visitor, or ``None``."""
    quote = promotion_gateway.quote(context["request"].user, package)
    if quote is None:
        return None
    regular = format_money(package.price, package.currency)
    promotional = format_money(quote.price, package.currency)
    return {
        "price_text": package.price_text.replace(regular, promotional, 1),
        "credit_price": quote.credit_price,
        "name": quote.promotion_name,
        "url": quote.offer_url,
        "ends_at": quote.ends_at,
    }


@register.filter
def file_detail_url(item: OrderItem) -> str:
    """Firmware page for a file order line, or an empty string."""
    if item.content_type_id is None:
        return ""
    links = entitlements.file_links(item.content_type_id, item.object_id)
    return links[0] if links else ""
