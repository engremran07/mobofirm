"""Scheduled wallet integrity check (F-020)."""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

from .reconcile import log_report, reconcile

logger = logging.getLogger(__name__)

# The run stops between batches before Celery's soft limit, so a huge ledger ends as an
# explicit "incomplete" report (logged as a warning) rather than a killed worker.
SOFT_TIME_LIMIT = 540
HARD_TIME_LIMIT = 600
RUN_BUDGET_SECONDS = 480


@shared_task(
    name="apps.wallet.tasks.reconcile_wallets",
    acks_late=True,
    soft_time_limit=SOFT_TIME_LIMIT,
    time_limit=HARD_TIME_LIMIT,
)
def reconcile_wallets() -> dict[str, int | bool]:
    """Nightly: compare every wallet counter with its ledger and alert on drift.

    Read-only by design. A drift is logged at ERROR with structured fields
    (``alert=wallet_reconcile_drift``) for the log pipeline to page on; repairing it
    is a separate audited action (``repair_wallet`` management command).
    """
    try:
        report = reconcile(max_seconds=RUN_BUDGET_SECONDS)
    except SoftTimeLimitExceeded:
        logger.error(
            "wallet reconcile hit the soft time limit",
            extra={"alert": "wallet_reconcile_timeout"},
        )
        return {"complete": False, "timed_out": True}
    log_report(report)
    return report.summary()
