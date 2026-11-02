"""
Notification System Admin Dashboard
Custom admin view for managing the enterprise notification system
"""

import logging
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone

logger = logging.getLogger(__name__)


@staff_member_required
def notification_dashboard(request):
    """
    Comprehensive notification system dashboard for admins.
    """
    from apps.users.models import (
        CustomUser,
        Notification,
        NotificationPreferences,
        PushSubscription,
    )

    context = {}

    try:
        # User Statistics
        total_users = CustomUser.objects.filter(is_active=True).count()
        users_with_prefs = NotificationPreferences.objects.count()
        users_with_push = (
            PushSubscription.objects.filter(is_active=True)
            .values("user")
            .distinct()
            .count()
        )

        context["user_stats"] = {
            "total": total_users,
            "with_preferences": users_with_prefs,
            "with_push": users_with_push,
            "prefs_percentage": round(
                (users_with_prefs / total_users * 100) if total_users > 0 else 0, 1
            ),
            "push_percentage": round(
                (users_with_push / total_users * 100) if total_users > 0 else 0, 1
            ),
        }

        # Notification Statistics
        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)
        last_30d = now - timedelta(days=30)

        total_notifications = Notification.objects.count()
        unread_notifications = Notification.objects.filter(is_read=False).count()
        notifications_24h = Notification.objects.filter(
            created_at__gte=last_24h
        ).count()
        notifications_7d = Notification.objects.filter(created_at__gte=last_7d).count()
        notifications_30d = Notification.objects.filter(
            created_at__gte=last_30d
        ).count()

        context["notification_stats"] = {
            "total": total_notifications,
            "unread": unread_notifications,
            "read_rate": round(
                (
                    (total_notifications - unread_notifications)
                    / total_notifications
                    * 100
                )
                if total_notifications > 0
                else 0,
                1,
            ),
            "last_24h": notifications_24h,
            "last_7d": notifications_7d,
            "last_30d": notifications_30d,
            "avg_per_day": round(notifications_30d / 30, 1),
        }

        # Notification Types Distribution
        type_distribution = (
            Notification.objects.values("action_type")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        context["type_distribution"] = type_distribution

        # Push Subscription Statistics
        total_subscriptions = PushSubscription.objects.count()
        active_subscriptions = PushSubscription.objects.filter(is_active=True).count()
        subscriptions_24h = PushSubscription.objects.filter(
            created_at__gte=last_24h
        ).count()

        # Browser breakdown
        browser_stats = []
        for browser_name in ["Chrome", "Firefox", "Safari", "Edge", "Opera"]:
            count = PushSubscription.objects.filter(
                is_active=True, user_agent__icontains=browser_name
            ).count()
            if count > 0:
                browser_stats.append(
                    {
                        "name": browser_name,
                        "count": count,
                        "percentage": round(
                            (count / active_subscriptions * 100)
                            if active_subscriptions > 0
                            else 0,
                            1,
                        ),
                    }
                )

        context["push_stats"] = {
            "total": total_subscriptions,
            "active": active_subscriptions,
            "inactive": total_subscriptions - active_subscriptions,
            "new_24h": subscriptions_24h,
            "browser_breakdown": browser_stats,
        }

        # Preference Statistics
        if users_with_prefs > 0:
            prefs = NotificationPreferences.objects.all()

            email_freq_stats = (
                prefs.values("email_frequency")
                .annotate(count=Count("id"))
                .order_by("-count")
            )

            context["preference_stats"] = {
                "email_comments_enabled": prefs.filter(email_comments=True).count(),
                "email_replies_enabled": prefs.filter(email_replies=True).count(),
                "email_mentions_enabled": prefs.filter(email_mentions=True).count(),
                "push_enabled": prefs.filter(push_enabled=True).count(),
                "quiet_hours_enabled": prefs.filter(quiet_hours_enabled=True).count(),
                "frequency_breakdown": list(email_freq_stats),
            }
        else:
            context["preference_stats"] = None

        # Recent Activity
        recent_notifications = Notification.objects.select_related(
            "recipient", "actor"
        ).order_by("-created_at")[:20]

        context["recent_notifications"] = recent_notifications

        # Top Users by Notifications Received
        top_recipients = (
            Notification.objects.values("recipient__email", "recipient__username")
            .annotate(total=Count("id"), unread=Count("id", filter=Q(is_read=False)))
            .order_by("-total")[:10]
        )

        context["top_recipients"] = top_recipients

        # System Health Checks
        health_checks = []

        # Check if VAPID keys configured
        from django.conf import settings

        vapid_configured = bool(
            getattr(settings, "VAPID_PUBLIC_KEY", None)
            and getattr(settings, "VAPID_PRIVATE_KEY", None)
        )
        health_checks.append(
            {
                "name": "VAPID Keys Configured",
                "status": vapid_configured,
                "message": "Push notifications ready"
                if vapid_configured
                else "Configure VAPID keys in settings",
            }
        )

        # Check if pywebpush installed
        try:
            import pywebpush  # type: ignore[import-not-found]  # noqa: F401

            pywebpush_installed = True
        except ImportError:
            pywebpush_installed = False

        health_checks.append(
            {
                "name": "pywebpush Library",
                "status": pywebpush_installed,
                "message": "Push library installed"
                if pywebpush_installed
                else "Install pywebpush: pip install pywebpush",
            }
        )

        # Check notification system enabled
        from apps.users.models import UsersSettings

        notif_enabled = UsersSettings.get_solo().enable_notifications
        health_checks.append(
            {
                "name": "Notification System",
                "status": notif_enabled,
                "message": "System active"
                if notif_enabled
                else "System disabled in settings",
            }
        )

        context["health_checks"] = health_checks

    except Exception as exc:
        logger.exception("Failed to generate notification dashboard: %s", exc)
        context["error"] = str(exc)

    return render(request, "admin/users/notification_dashboard.html", context)
