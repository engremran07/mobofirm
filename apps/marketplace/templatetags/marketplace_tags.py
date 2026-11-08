"""Template seams that let other apps show marketplace information."""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled

from ..models import SellerProfile

register = template.Library()


@register.inclusion_tag("components/dashboard_tile.html", takes_context=True)
def marketplace_dashboard_tile(context: dict[str, Any]) -> dict[str, Any]:
    """Account dashboard tile: seller state and listing count."""
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("marketplace"):
        return {}
    seller = SellerProfile.objects.filter(user=user, is_active=True).first()
    if seller is None:
        return {
            "url": reverse("marketplace:become_seller"),
            "label": "Marketplace",
            "value": "Become a seller",
            "icon": "store",
            "hint": "Apply for a seller profile",
        }
    if not seller.verified:
        return {
            "url": reverse("marketplace:application_status"),
            "label": "Marketplace",
            "value": "Application",
            "icon": "store",
            "hint": "See your seller application status",
        }
    count = seller.listings.count()
    return {
        "url": reverse("marketplace:dashboard"),
        "label": "Seller dashboard",
        "value": count,
        "icon": "store",
        "hint": "Listings · approved seller",
    }
