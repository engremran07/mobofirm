from __future__ import annotations

from celery import shared_task

from apps.bounty.services import expire_due


@shared_task(name="apps.bounty.tasks.expire_bounties")
def expire_bounties() -> int:
    """Hourly: refund and expire bounties past their deadline."""
    return expire_due()
