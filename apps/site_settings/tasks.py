"""Periodic maintenance owned by the site settings app."""

from __future__ import annotations

from celery import shared_task

from .editor_cleanup import cleanup_orphan_images


@shared_task(name="apps.site_settings.tasks.cleanup_editor_images")
def cleanup_editor_images() -> dict[str, object]:
    """Delete editor images no stored content references (24 h grace period)."""
    result = cleanup_orphan_images()
    return {"deleted": result.deleted, "kept": result.kept, "aborted": result.aborted}
