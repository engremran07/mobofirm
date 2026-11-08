"""Public service contract of gamification (other apps import only from here).

* ``award(user, event_key, *, key, reference="")`` - idempotent XP grant.
* ``total_xp(user)``, ``level_of(user)`` and ``badges_of(user)`` for display.
* Event bus: publish ``gamification.award_xp`` with ``{"user_id", "event_key",
  "key"}`` instead of importing this module.
"""

from __future__ import annotations

from typing import Any

from apps.gamification import services
from apps.gamification.models import Badge, Level, UserBadge, XPTransaction


def award(
    user: Any, event_key: str, *, key: str, reference: str = ""
) -> XPTransaction | None:
    return services.award(user, event_key, key=key, reference=reference)


def total_xp(user: Any) -> int:
    return services.total_xp(user.pk)


def level_of(user: Any) -> Level | None:
    return services.level_for(services.total_xp(user.pk))


def badges_of(user: Any) -> list[Badge]:
    return [
        award_row.badge
        for award_row in UserBadge.objects.filter(user=user).select_related("badge")
    ]
