from __future__ import annotations

from typing import Any

from django import template

from apps.gamification import services
from apps.gamification.models import UserBadge

register = template.Library()


@register.inclusion_tag("gamification/_user_badges.html")
def user_badges(user: Any, limit: int = 8) -> dict[str, Any]:
    """``{% load gamification_tags %}{% user_badges profile_user %}``: level and badges."""
    if user is None or not getattr(user, "pk", None):
        return {"show": False}
    held = list(
        UserBadge.objects.filter(user=user).select_related("badge")[: int(limit)]
    )
    summary = services.progress_for(user.pk)
    return {
        "show": bool(held) or summary.xp > 0,
        "held": held,
        "progress": summary,
    }
