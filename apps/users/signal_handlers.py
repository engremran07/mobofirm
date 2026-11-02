"""
Signal handlers for users app
Responds to notification signals without creating circular dependencies.
"""

import logging

from django.dispatch import receiver

from apps.core.signals import (
    notification_send_requested,
    user_login_success,
    user_registered,
)

logger = logging.getLogger(__name__)


@receiver(notification_send_requested)
def send_notifications(
    sender, user_ids, title, message, notification_type="info", **kwargs
):
    """Handle notification send requests from other apps"""
    try:
        from apps.users.models import CustomUser, Notification

        users = CustomUser.objects.filter(id__in=user_ids)
        notifications = [
            Notification(
                recipient=user,
                title=title,
                message=message,
                notification_type=notification_type,
            )
            for user in users
        ]

        created = Notification.objects.bulk_create(notifications)
        logger.info(f"Sent {len(created)} notifications")
        return len(created)
    except Exception as exc:
        logger.exception(f"Failed to send notifications: {exc}")
        return 0


@receiver(user_registered)
def send_welcome_notification(sender, user, request, **kwargs):
    """Send welcome notification when user registers"""
    try:
        from apps.users.models import Notification

        Notification.objects.create(
            recipient=user,
            title="Welcome!",
            message=f"Welcome to our platform, {user.username}!",
            notification_type="info",
        )
        logger.info(f"Sent welcome notification to user: {user.pk}")
    except Exception as exc:
        logger.exception(f"Failed to send welcome notification: {exc}")


@receiver(user_login_success)
def track_login(sender, user, request, **kwargs):
    """Track successful login"""
    try:
        from django.core.cache import cache

        cache_key = f"user_last_login_{user.pk}"
        cache.set(cache_key, user.last_login, 3600)
        logger.debug(f"Tracked login for user: {user.pk}")
    except Exception as exc:
        logger.exception(f"Failed to track login: {exc}")
