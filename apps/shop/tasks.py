from __future__ import annotations

from celery import shared_task

from .services import expire_overdue_packages


@shared_task(name="apps.shop.tasks.expire_packages")
def expire_packages() -> dict[str, int]:
    """Expire packages and subscriptions past their end date."""
    return expire_overdue_packages()
