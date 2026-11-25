"""Scheduled cleanup for retained device audit events."""

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db import DatabaseError
from django.utils import timezone

from apps.devices.models import DeviceEvent

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def purge_old_device_events(self) -> dict[str, Any]:
    """Delete device audit events older than the configured retention window."""
    from django.conf import settings

    days = int(getattr(settings, "DEVICE_EVENT_RETENTION_DAYS", 90))
    if days <= 0:
        return {"status": "disabled", "deleted": 0}
    cutoff = timezone.now() - timedelta(days=days)
    try:
        deleted, _ = DeviceEvent.objects.filter(created_at__lt=cutoff).delete()
    except DatabaseError as exc:
        logger.exception("purge_old_device_events failed")
        raise self.retry(exc=exc, countdown=120 * (2**self.request.retries)) from exc
    logger.info("purge_old_device_events: deleted %d events", deleted)
    return {"status": "success", "deleted": deleted}
