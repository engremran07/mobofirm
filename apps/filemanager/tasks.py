from __future__ import annotations

from celery import shared_task

from . import services


@shared_task(name="apps.filemanager.tasks.purge_expired_shares")
def purge_expired_shares() -> int:
    """Deactivate share links whose expiry passed."""
    return services.expire_shares()


@shared_task(name="apps.filemanager.tasks.purge_deleted_files")
def purge_deleted_files(older_than_days: int = 30) -> int:
    """Remove bytes of files that were soft-deleted longer ago than the retention."""
    return services.purge_deleted_files(older_than_days)
