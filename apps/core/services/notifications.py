"""
Notification Service Implementation
Provides notification functionality without direct model imports in core.
"""

import logging

from django.apps import apps
from django.db import DatabaseError

from apps.core.interfaces import NotificationService

logger = logging.getLogger(__name__)


class DjangoNotificationService(NotificationService):
    """Implementation that uses Django's apps registry for lazy model loading"""

    def send(
        self,
        user_id: int,
        title: str,
        message: str,
        notification_type: str = "info",
        **kwargs,
    ) -> bool:
        """Send a notification to a user; ``notification_type`` is the priority."""
        Notification = apps.get_model("users", "Notification")
        CustomUser = apps.get_model("users", "CustomUser")

        user = CustomUser.objects.filter(id=user_id).first()
        if not user:
            return False

        try:
            Notification.objects.create(
                recipient=user,
                title=title,
                message=message,
                priority=notification_type,
                **kwargs,
            )
        except DatabaseError:
            logger.exception("Notification for user %s could not be stored", user_id)
            return False
        return True

    def send_bulk(
        self,
        user_ids: list[int],
        title: str,
        message: str,
        notification_type: str = "info",
    ) -> int:
        """Send notification to multiple users, returns count sent"""
        Notification = apps.get_model("users", "Notification")
        CustomUser = apps.get_model("users", "CustomUser")

        users = CustomUser.objects.filter(id__in=user_ids)
        notifications = [
            Notification(
                recipient=user,
                title=title,
                message=message,
                priority=notification_type,
            )
            for user in users
        ]
        try:
            Notification.objects.bulk_create(notifications)
        except DatabaseError:
            logger.exception("Bulk notification could not be stored")
            return 0
        return len(notifications)


# Global instance
notification_service = DjangoNotificationService()
