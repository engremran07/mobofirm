"""
Notification Service Implementation
Provides notification functionality without direct model imports in core.
"""

from django.apps import apps

from apps.core.interfaces import NotificationService


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
        """Send a notification to a user"""
        try:
            Notification = apps.get_model("users", "Notification")
            CustomUser = apps.get_model("users", "CustomUser")

            user = CustomUser.objects.filter(id=user_id).first()
            if not user:
                return False

            Notification.objects.create(
                recipient=user,
                title=title,
                message=message,
                notification_type=notification_type,
                **kwargs,
            )
            return True
        except Exception:
            return False

    def send_bulk(
        self,
        user_ids: list[int],
        title: str,
        message: str,
        notification_type: str = "info",
    ) -> int:
        """Send notification to multiple users, returns count sent"""
        try:
            Notification = apps.get_model("users", "Notification")
            CustomUser = apps.get_model("users", "CustomUser")

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
            Notification.objects.bulk_create(notifications)
            return len(notifications)
        except Exception:
            return 0


# Global instance
notification_service = DjangoNotificationService()
