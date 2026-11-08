from __future__ import annotations

from celery import shared_task

from apps.distribution import automation
from apps.distribution.services import process_due_jobs


@shared_task(name="apps.distribution.tasks.process_share_jobs")
def process_share_jobs() -> dict[str, int]:
    """Every minute: deliver due shares, retrying transient failures.

    Retention purge and evergreen scheduling piggy-back here and run at most once an
    hour (``automation.housekeeping`` throttles itself).
    """
    summary = process_due_jobs()
    automation.housekeeping()
    return {
        "sent": summary.sent,
        "retried": summary.retried,
        "failed": summary.failed,
        "deferred": summary.deferred,
    }


@shared_task(name="apps.distribution.tasks.run_housekeeping")
def run_housekeeping() -> dict[str, int]:
    """Force the retention purge and evergreen pass (staff or ops)."""
    return automation.housekeeping(force=True)
