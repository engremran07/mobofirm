"""
Signal handlers for users app
Responds to notification signals without creating circular dependencies.
"""

import logging

from allauth.account.signals import user_signed_up
from django.db import transaction
from django.dispatch import receiver

from apps.core.signals import (
    notification_send_requested,
    user_login_success,
    user_registered,
)

logger = logging.getLogger(__name__)


@receiver(notification_send_requested)
def send_notifications(
    sender, user_ids, title, message, action_type="system", priority="info", **kwargs
):
    """Handle notification send requests from other apps"""
    from apps.users.models import CustomUser, Notification
    from apps.users.services.notifications import broadcast_notification

    action_types = {value for value, _ in Notification.ACTION_TYPE_CHOICES}
    priorities = {value for value, _ in Notification.PRIORITY_CHOICES}
    if action_type not in action_types:
        raise ValueError(f"Unsupported notification action type: {action_type}")
    if priority not in priorities:
        raise ValueError(f"Unsupported notification priority: {priority}")

    created = broadcast_notification(
        CustomUser.objects.filter(pk__in=user_ids),
        title=title,
        message=message,
        level=priority,
        channel="web",
        action_type=action_type,
    )
    logger.info("Sent %s notifications", created)
    return created


@receiver(user_registered)
@receiver(user_signed_up)
def send_welcome_notification(sender, user, request, **kwargs):
    """Send welcome notification when user registers"""
    try:
        from apps.users.models import Notification

        with transaction.atomic():
            locked_user = type(user).objects.select_for_update().get(pk=user.pk)
            _, created = Notification.objects.get_or_create(
                recipient=locked_user,
                title="Welcome!",
                action_type="system",
                defaults={
                    "message": f"Welcome to our platform, {locked_user.username}!",
                    "priority": "info",
                    "channel": "web",
                },
            )
        if created:
            logger.info("Sent welcome notification to user %s", user.pk)
    except Exception as exc:
        logger.exception("Failed to send welcome notification: %s", exc)


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
