"""apps.users.tasks — periodic account-erasure runner."""

from __future__ import annotations

import logging

from celery import shared_task

from apps.users.services.erasure import run_due_erasures

logger = logging.getLogger(__name__)


@shared_task(
    name="users.process_account_erasures",
    acks_late=True,
    soft_time_limit=240,
    time_limit=300,
)
def process_account_erasures(limit: int = 25) -> dict[str, int]:
    """Execute confirmed erasure requests whose grace period has ended.

    Safe to run concurrently and to repeat: each request is claimed under a row lock,
    attempts are counted and capped, and a finished request is never run twice.
    The summary holds counts only (no personal data).
    """
    outcome = run_due_erasures(limit=limit)
    if outcome["processed"]:
        logger.info("Account erasures processed: %s", outcome)
    return outcome
