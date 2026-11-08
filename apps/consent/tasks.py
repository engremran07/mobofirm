from __future__ import annotations

from celery import shared_task

from apps.consent.services import purge_old_records


@shared_task(name="apps.consent.tasks.purge_old_records")
def purge_consent_records() -> int:
    """Daily: drop anonymised consent records past the retention window."""
    return purge_old_records()
