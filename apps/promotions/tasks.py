from __future__ import annotations

from celery import shared_task

from . import lifecycle


@shared_task(name="apps.promotions.tasks.sync_statuses")
def sync_statuses() -> dict[str, int]:
    """Start, end and announce promotions whose window opened or closed."""
    return lifecycle.sync_statuses()


@shared_task(name="apps.promotions.tasks.notify_audience")
def notify_audience_task(promotion_id: int) -> int:
    """Notify a promotion's audience once (resumable, idempotent)."""
    return lifecycle.notify_audience(promotion_id)
