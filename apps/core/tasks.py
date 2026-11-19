"""Periodic maintenance tasks owned by the core app."""

from __future__ import annotations

from celery import shared_task
from django.core.management import call_command

# Bounded retention work: a soft limit lets the task stop cleanly, the hard limit
# kills a stuck database call so the worker slot is released. The command is
# idempotent (it only deletes rows past the cutoff), so a killed run is simply
# resumed by the next daily run, and acks_late makes a lost worker redeliver it.
CLEANUP_SOFT_TIME_LIMIT = 25 * 60
CLEANUP_TIME_LIMIT = 30 * 60


@shared_task(
    name="apps.core.tasks.cleanup_old_data",
    soft_time_limit=CLEANUP_SOFT_TIME_LIMIT,
    time_limit=CLEANUP_TIME_LIMIT,
    acks_late=True,
)
def cleanup_old_data(days: int = 90) -> None:
    """Run the bounded retention command (sessions, API tokens, old records)."""
    call_command("cleanup_old_data", days=days)
