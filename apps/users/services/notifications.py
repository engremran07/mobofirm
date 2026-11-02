from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.users.models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()


def notifications_enabled() -> bool:
    """
    Global kill-switch for notifications (UsersSettings.enable_notifications).
    Falls back to True if settings or singleton are unavailable.
    """
    try:
        from apps.users.models import UsersSettings  # local import to avoid circulars

        us = UsersSettings.get_solo()
        return bool(getattr(us, "enable_notifications", True))
    except Exception as exc:
        logger.debug("notifications_enabled fallback (default True): %s", exc)
        return True


def send_notification(
    recipient: User,
    title: str,
    message: str,
    level: str = "info",  # mapped to model.priority
    url: str | None = None,
    actor: User | None = None,
    channel: str | None = None,
    action_type: str | None = "system",
    icon: str | None = None,
) -> Notification | None:
    """
    Safely create a notification for a user.
    Returns the Notification instance or None on error.
    Respects user notification preferences.
    """

    if not notifications_enabled():
        return None

    if not recipient:
        return None

    # Check user preferences
    try:
        from apps.users.models import NotificationPreferences

        prefs = NotificationPreferences.objects.filter(user=recipient).first()

        if prefs:
            # Check if it's quiet hours
            if prefs.is_quiet_hours_now() and level != "critical":
                logger.debug(
                    "Skipping notification for user %s during quiet hours", recipient.pk
                )
                return None

            # Check if web notifications are enabled for this type
            if channel == "web" and not prefs.should_send_web(action_type or ""):
                logger.debug(
                    "Web notifications disabled for user %s, type %s",
                    recipient.pk,
                    action_type,
                )
                return None
    except Exception as exc:
        logger.debug("Failed to check notification preferences: %s", exc)

    try:
        with transaction.atomic():
            n = Notification.objects.create(
                recipient=recipient,
                title=title[:255],
                message=message,
                url=url[:500] if url else None,
                priority=level,
                channel=channel,
                action_type=action_type or "system",
                icon=icon or "",
                actor=actor,
            )

            # Send push notification if enabled
            _send_push_notification(n, recipient, action_type or "")

            return n

    except Exception as exc:
        logger.exception(
            "Failed to create notification for user %s: %s",
            getattr(recipient, "pk", None),
            exc,
        )
        return None


def broadcast_notification(
    recipients,
    title: str,
    message: str,
    level: str = "info",
    url: str | None = None,
    actor: User | None = None,
    channel: str | None = None,
    action_type: str | None = "system",
    icon: str | None = None,
) -> int:
    """
    Bulk-create notifications for an iterable/QuerySet of users.
    Returns count created.
    """
    if not notifications_enabled():
        return 0

    try:
        user_list = [
            r for r in recipients if r is not None and getattr(r, "is_active", True)
        ]
    except Exception as exc:
        logger.exception("broadcast_notification recipients iteration failed: %s", exc)
        return 0

    if not user_list:
        return 0

    now = timezone.now()
    payloads = [
        Notification(
            recipient=r,
            title=title[:255],
            message=message,
            url=url[:500] if url else None,
            priority=level,
            channel=channel,
            action_type=action_type or "system",
            icon=icon or "",
            actor=actor,
            created_at=now,
        )
        for r in user_list
    ]

    try:
        objs = Notification.objects.bulk_create(payloads)
        return len(objs)
    except Exception as exc:
        logger.exception("broadcast_notification failed: %s", exc)
        return 0


def _send_push_notification(
    notification: Notification, recipient: User, action_type: str
) -> None:
    """
    Send push notification to user's subscribed devices.
    """
    try:
        from apps.users.models import NotificationPreferences, PushSubscription

        # Check if push is enabled
        prefs = NotificationPreferences.objects.filter(user=recipient).first()
        if not prefs or not prefs.push_enabled:
            return

        # Get active subscriptions
        subscriptions = PushSubscription.objects.filter(user=recipient, is_active=True)
        if not subscriptions.exists():
            return

        # Prepare push payload
        payload = {
            "title": notification.title,
            "body": notification.message[:100],
            "icon": f"/static/img/{notification.icon}.png"
            if notification.icon
            else "/static/img/logo.png",
            "badge": "/static/img/logo.png",
            "tag": f"notification-{notification.pk}",
            "url": notification.url or "/users/notifications/",
            "notificationId": notification.pk,
        }

        # Try sending via pywebpush (if installed)
        try:
            import json

            from pywebpush import (  # type: ignore[import-not-found]
                WebPushException,
                webpush,
            )

            vapid_claims = {
                "sub": f"mailto:{getattr(settings, 'VAPID_ADMIN_EMAIL', 'admin@example.com')}"
            }

            for subscription in subscriptions:
                try:
                    webpush(
                        subscription_info=subscription.to_dict(),
                        data=json.dumps(payload),
                        vapid_private_key=getattr(settings, "VAPID_PRIVATE_KEY", ""),
                        vapid_claims=vapid_claims,
                    )
                    subscription.last_used_at = timezone.now()
                    subscription.save(update_fields=["last_used_at"])
                except WebPushException as exc:
                    logger.warning(
                        "Push failed for subscription %s: %s", subscription.pk, exc
                    )
                    if exc.response and exc.response.status_code in [404, 410]:
                        # Subscription expired
                        subscription.is_active = False
                        subscription.save(update_fields=["is_active"])

        except ImportError:
            logger.debug("pywebpush not installed, skipping push notifications")

    except Exception as exc:
        logger.exception("Failed to send push notification: %s", exc)
