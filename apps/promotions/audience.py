"""Who a promotion is for: per-user checks and the matching user queryset."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.utils import timezone

from . import gateways
from .models import Audience, Promotion

NEW_USER_DAYS = 30


def is_verified(user: Any) -> bool:
    return bool(getattr(user, "email_verified_at", None))


def is_authenticated(user: Any) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False))


def audience_matches(user: Any, promotion: Promotion) -> bool:
    """Whether ``user`` (possibly anonymous) belongs to the promotion's audience."""
    audience = promotion.audience
    if audience == Audience.EVERYONE:
        return True
    if not is_authenticated(user):
        return False
    if audience == Audience.VERIFIED_EMAIL:
        return is_verified(user)
    if audience == Audience.NEW_USERS:
        joined = getattr(user, "date_joined", None)
        return joined is not None and joined >= timezone.now() - timedelta(
            days=NEW_USER_DAYS
        )
    if audience == Audience.SELLERS:
        return gateways.is_seller(user)
    if audience == Audience.PACKAGE_HOLDERS:
        return gateways.live_package(user) is not None
    return False


def audience_label(promotion: Promotion) -> str:
    if promotion.audience == Audience.NEW_USERS:
        return f"New users (joined in the last {NEW_USER_DAYS} days)"
    return str(promotion.get_audience_display())


def audience_users(promotion: Promotion) -> QuerySet[Any]:
    """Active accounts in the audience, in a stable order for batch work."""
    users = get_user_model().objects.filter(is_active=True)
    audience = promotion.audience
    if audience == Audience.VERIFIED_EMAIL:
        users = users.filter(email_verified_at__isnull=False)
    elif audience == Audience.NEW_USERS:
        users = users.filter(
            date_joined__gte=timezone.now() - timedelta(days=NEW_USER_DAYS)
        )
    elif audience == Audience.SELLERS:
        users = users.filter(pk__in=gateways.seller_user_ids())
    elif audience == Audience.PACKAGE_HOLDERS:
        users = users.filter(pk__in=gateways.package_holder_ids())
    return users.order_by("pk")
