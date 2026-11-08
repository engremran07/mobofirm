"""User notifications for firmware and catalogue decisions."""

from __future__ import annotations

import logging

from apps.core.signals import notification_send_requested

logger = logging.getLogger(__name__)


def notify_users(
    user_ids: list[int],
    *,
    sender: type,
    title: str,
    message: str,
    priority: str,
    subject: object,
) -> None:
    """Send a moderation notification; receiver failures are logged, not raised.

    ``subject`` only identifies the record in the log line.
    """
    if not user_ids:
        return
    for receiver, response in notification_send_requested.send_robust(
        sender=sender,
        user_ids=user_ids,
        action_type="moderation",
        priority=priority,
        title=title,
        message=message,
    ):
        if isinstance(response, Exception):
            logger.error(
                "Notification receiver %r failed for %s: %s",
                receiver,
                subject,
                response,
            )
