from __future__ import annotations

import json
import logging
from importlib import import_module
from importlib.util import find_spec

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.users.models import CustomUser, Notification

logger = logging.getLogger(__name__)


def notifications_enabled() -> bool:
    """Global kill-switch for notifications (UsersSettings.enable_notifications)."""
    from apps.users.models import UsersSettings  # local import to avoid circulars

    return bool(UsersSettings.get_solo().enable_notifications)


def push_delivery_available() -> bool:
    """Web push needs VAPID keys and the optional ``pywebpush`` package."""
    return bool(
        getattr(settings, "VAPID_PUBLIC_KEY", "")
        and getattr(settings, "VAPID_PRIVATE_KEY", "")
        and find_spec("pywebpush") is not None
    )


def send_notification(
    recipient: CustomUser,
    title: str,
    message: str,
    level: str = "info",  # mapped to model.priority
    url: str | None = None,
    actor: CustomUser | None = None,
    channel: str | None = None,
    action_type: str | None = "system",
    icon: str | None = None,
) -> Notification | None:
    """
    Create a notification for a user, honouring their preferences.

    Returns the Notification instance, or None when it was suppressed by the
    global switch, quiet hours, preferences, or a database failure (logged).
    """

    if not notifications_enabled():
        return None

    if not recipient:
        return None

    delivery_channel = channel or "web"

    from apps.users.models import NotificationPreferences

    prefs = NotificationPreferences.objects.filter(user=recipient).first()
    if prefs:
        if prefs.is_quiet_hours_now() and level != "critical":
            logger.debug(
                "Skipping notification for user %s during quiet hours", recipient.pk
            )
            return None
        if delivery_channel == "web" and not prefs.should_send_web(action_type or ""):
            logger.debug(
                "Web notifications disabled for user %s, type %s",
                recipient.pk,
                action_type,
            )
            return None

    try:
        with transaction.atomic():
            n = Notification.objects.create(
                recipient=recipient,
                title=title[:255],
                message=message,
                url=url[:500] if url else None,
                priority=level,
                channel=delivery_channel,
                action_type=action_type or "system",
                icon=icon or "",
                actor=actor,
            )
    except DatabaseError:
        logger.exception(
            "Failed to create notification for user %s", getattr(recipient, "pk", None)
        )
        return None

    _send_push_notification(n, recipient)
    return n


def broadcast_notification(
    recipients,
    title: str,
    message: str,
    level: str = "info",
    url: str | None = None,
    actor: CustomUser | None = None,
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

    user_list = [
        r for r in recipients if r is not None and getattr(r, "is_active", True)
    ]
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
            channel=channel or "web",
            action_type=action_type or "system",
            icon=icon or "",
            actor=actor,
            created_at=now,
        )
        for r in user_list
    ]

    try:
        objs = Notification.objects.bulk_create(payloads)
    except DatabaseError:
        logger.exception("broadcast_notification failed")
        return 0
    return len(objs)


def _send_push_notification(notification: Notification, recipient: CustomUser) -> None:
    """Send a push notification to the user's subscribed browsers, if enabled."""
    if not push_delivery_available():
        return

    from apps.users.models import NotificationPreferences, PushSubscription

    prefs = NotificationPreferences.objects.filter(user=recipient).first()
    if not prefs or not prefs.push_enabled:
        return

    subscriptions = list(
        PushSubscription.objects.filter(user=recipient, is_active=True)
    )
    if not subscriptions:
        return

    pywebpush = import_module("pywebpush")
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
    vapid_claims = {
        "sub": f"mailto:{getattr(settings, 'VAPID_ADMIN_EMAIL', settings.DEFAULT_FROM_EMAIL)}"
    }

    for subscription in subscriptions:
        try:
            pywebpush.webpush(
                subscription_info=subscription.to_dict(),
                data=json.dumps(payload),
                vapid_private_key=getattr(settings, "VAPID_PRIVATE_KEY", ""),
                vapid_claims=vapid_claims,
            )
        except pywebpush.WebPushException as exc:
            logger.warning("Push failed for subscription %s: %s", subscription.pk, exc)
            if exc.response is not None and exc.response.status_code in (404, 410):
                subscription.is_active = False
                subscription.save(update_fields=["is_active"])
            continue
        subscription.last_used_at = timezone.now()
        subscription.save(update_fields=["last_used_at"])
