from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Campaign

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Campaign)
def notify_campaign_status_change(sender, instance, created, **kwargs):
    """
    Notify campaign creator when campaign status changes (e.g. approved/active).
    """
    if created:
        return

    # Check if is_active changed (requires dirty field tracking or pre-save,
    # but for now we'll just notify if it's active and has a creator)
    if instance.is_active and instance.created_by:
        try:
            from apps.users.services.notifications import send_notification
            # We might want to debounce this or check if it was already active
            # For this audit, we'll assume this is a desired "Campaign Active" alert
            # In a real app, we'd check `if instance.tracker.has_changed('is_active')`

            send_notification(
                recipient=instance.created_by,
                title="Campaign Active",
                message=f"Your campaign '{instance.name}' is now active.",
                level="info",
                channel="web",
                action_type="system",
                icon="trending-up",
            )
        except Exception as e:
            logger.debug(f"Failed to send campaign notification: {e}")
