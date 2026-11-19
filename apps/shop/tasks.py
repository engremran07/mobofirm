from __future__ import annotations

from celery import shared_task

from . import ad_unlock
from .services import expire_overdue_packages

# One hourly sweep of two indexed UPDATEs: far below these limits, they only stop a
# wedged database connection from holding a worker slot (F-011).
SWEEP_SOFT_TIME_LIMIT = 120
SWEEP_TIME_LIMIT = 180


@shared_task(
    name="apps.shop.tasks.expire_packages",
    soft_time_limit=SWEEP_SOFT_TIME_LIMIT,
    time_limit=SWEEP_TIME_LIMIT,
)
def expire_packages() -> dict[str, int]:
    """Expire packages past their end date and ad unlocks that ran out of time.

    Both sweeps are idempotent UPDATEs of rows whose deadline has passed, so a repeated
    or overlapping run changes nothing the first one did not.
    """
    result = expire_overdue_packages()
    result["unlock_tickets"] = ad_unlock.cancel_stale()
    return result
