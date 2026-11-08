"""Promotion-owned banner rules plugged into the users banner service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.http import HttpRequest

from apps.core.app_service import app_enabled
from apps.users.models import Announcement

from . import gateways
from .audience import audience_matches, is_authenticated
from .effects import has_live_grant
from .grants import bump


def banner_gate(user: Any, announcements: Sequence[Announcement]) -> list[Announcement]:
    """Show a promotion's banner only while it runs, to its audience, until claimed."""
    owned = gateways.promotion_for_announcements([a.pk for a in announcements])
    if not owned:
        return list(announcements)
    enabled = app_enabled("promotions")
    visible: list[Announcement] = []
    for announcement in announcements:
        promotion = owned.get(announcement.pk)
        if promotion is None:
            visible.append(announcement)
            continue
        if not enabled or not promotion.is_live:
            continue
        if not audience_matches(user, promotion):
            continue
        if (
            is_authenticated(user)
            and promotion.is_claimable
            and has_live_grant(user, promotion)
        ):
            continue
        visible.append(announcement)
    return visible


def banner_view_hook(
    request: HttpRequest, announcements: Sequence[Announcement]
) -> None:
    """Count a banner impression once per signed-in session."""
    owned = gateways.promotion_for_announcements([a.pk for a in announcements])
    for promotion in owned.values():
        bump(promotion.pk, banner_views=1)
