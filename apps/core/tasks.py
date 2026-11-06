"""Periodic maintenance tasks owned by the core app."""

from __future__ import annotations

from celery import shared_task
from django.core.management import call_command


@shared_task(name="apps.core.tasks.cleanup_old_data")
def cleanup_old_data(days: int = 90) -> None:
    """Run the bounded retention command (sessions, API tokens, old records)."""
    call_command("cleanup_old_data", days=days)
