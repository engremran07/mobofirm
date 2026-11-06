"""Data behind the admin notification dashboard (typed, ORM-aggregated)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from importlib.util import find_spec
from typing import Any

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from apps.users.models import (
    CustomUser,
    Notification,
    NotificationPreferences,
    PushSubscription,
    UsersSettings,
)

BROWSERS = ("Chrome", "Firefox", "Safari", "Edge", "Opera")


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    ok: bool
    message: str


def _percentage(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def health_checks() -> list[HealthCheck]:
    vapid = bool(
        getattr(settings, "VAPID_PUBLIC_KEY", "")
        and getattr(settings, "VAPID_PRIVATE_KEY", "")
    )
    library = find_spec("pywebpush") is not None
    enabled = UsersSettings.get_solo().enable_notifications
    return [
        HealthCheck(
            "VAPID keys",
            vapid,
            "Push notifications are ready"
            if vapid
            else "Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY",
        ),
        HealthCheck(
            "pywebpush library",
            library,
            "Push library installed" if library else "pywebpush is not installed",
        ),
        HealthCheck(
            "Notification system",
            enabled,
            "System active" if enabled else "Disabled in Users settings",
        ),
    ]


def notification_dashboard_context() -> dict[str, Any]:
    now = timezone.now()
    day, week, month = (now - timedelta(days=d) for d in (1, 7, 30))

    total_users = CustomUser.objects.filter(is_active=True).count()
    with_preferences = NotificationPreferences.objects.count()
    with_push = (
        PushSubscription.objects.filter(is_active=True)
        .values("user")
        .distinct()
        .count()
    )

    totals = Notification.objects.aggregate(
        total=Count("pk"),
        unread=Count("pk", filter=Q(is_read=False)),
        last_24h=Count("pk", filter=Q(created_at__gte=day)),
        last_7d=Count("pk", filter=Q(created_at__gte=week)),
        last_30d=Count("pk", filter=Q(created_at__gte=month)),
    )
    subscriptions = PushSubscription.objects.aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(is_active=True)),
        new_24h=Count("pk", filter=Q(created_at__gte=day)),
    )
    browser_counts = PushSubscription.objects.filter(is_active=True).aggregate(
        **{name: Count("pk", filter=Q(user_agent__icontains=name)) for name in BROWSERS}
    )

    return {
        "user_stats": {
            "total": total_users,
            "with_preferences": with_preferences,
            "with_push": with_push,
            "prefs_percentage": _percentage(with_preferences, total_users),
            "push_percentage": _percentage(with_push, total_users),
        },
        "notification_stats": {
            **totals,
            "read_rate": _percentage(
                totals["total"] - totals["unread"], totals["total"]
            ),
            "avg_per_day": round(totals["last_30d"] / 30, 1),
        },
        "push_stats": {
            **subscriptions,
            "inactive": subscriptions["total"] - subscriptions["active"],
            "browsers": [
                {
                    "name": name,
                    "count": count,
                    "percentage": _percentage(count, subscriptions["active"]),
                }
                for name, count in browser_counts.items()
                if count
            ],
        },
        "type_distribution": list(
            Notification.objects.values("action_type")
            .annotate(count=Count("pk"))
            .order_by("-count")[:10]
        ),
        "top_recipients": list(
            Notification.objects.values("recipient__email", "recipient__username")
            .annotate(total=Count("pk"), unread=Count("pk", filter=Q(is_read=False)))
            .order_by("-total")[:10]
        ),
        "recent_notifications": list(
            Notification.objects.select_related("recipient", "actor").order_by(
                "-created_at"
            )[:20]
        ),
        "health_checks": health_checks(),
    }
