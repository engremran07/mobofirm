"""Orphan cleanup of editor images: grace period, references, fail-closed."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.site_settings import editor_cleanup
from apps.site_settings.editor_cleanup import cleanup_orphan_images
from apps.site_settings.editor_models import EditorImage, EditorProfile
from apps.site_settings.tasks import cleanup_editor_images

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_media(settings: Any, tmp_path: Path) -> None:
    """Keep stored files in a throw-away directory."""
    settings.MEDIA_ROOT = tmp_path


def _image(age_days: int) -> EditorImage:
    image = EditorImage(context="guide_request", width=1, height=1, size_bytes=3)
    image.file.save("x.png", ContentFile(b"png"), save=False)
    image.save()
    EditorImage.objects.filter(pk=image.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return image


def _reference(monkeypatch: pytest.MonkeyPatch, *paths: str) -> None:
    """Register ``EditorProfile.placeholder`` as the stored-content field."""
    EditorProfile.objects.create(
        context="page", placeholder="".join(f"/media/{path} " for path in paths)
    )
    monkeypatch.setattr(
        editor_cleanup, "RICH_TEXT_FIELDS", ("site_settings.EditorProfile.placeholder",)
    )


def test_only_old_unreferenced_images_are_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh and referenced images survive; stale orphans (and files) go."""
    fresh, kept, orphan = _image(0), _image(5), _image(5)
    _reference(monkeypatch, kept.file.name)
    stored = Path(orphan.file.path)
    assert stored.is_file()
    dry = cleanup_orphan_images(dry_run=True)
    assert dry.deleted == 1
    assert EditorImage.objects.count() == 3
    result = cleanup_orphan_images()
    assert (result.deleted, result.kept) == (1, 1)
    assert set(EditorImage.objects.values_list("pk", flat=True)) == {fresh.pk, kept.pk}
    assert not stored.exists()


def test_cleanup_fails_closed_when_a_registered_field_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in the registry must never delete images."""
    orphan = _image(5)
    monkeypatch.setattr(
        editor_cleanup, "RICH_TEXT_FIELDS", ("site_settings.Nope.body",)
    )
    result = cleanup_orphan_images()
    assert result.deleted == 0
    assert result.aborted == "site_settings.Nope.body"
    assert EditorImage.objects.filter(pk=orphan.pk).exists()


def test_uninstalled_apps_in_the_registry_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fields of apps that are not installed hold no content."""
    orphan = _image(5)
    monkeypatch.setattr(editor_cleanup, "RICH_TEXT_FIELDS", ("ghost.Thing.body",))
    assert cleanup_orphan_images().deleted == 1
    assert not EditorImage.objects.filter(pk=orphan.pk).exists()


def test_registered_rich_text_fields_all_resolve() -> None:
    """Every entry of the real registry points at an installed model field."""
    assert editor_cleanup.referenced_paths() == set()


def test_celery_task_reports_the_result() -> None:
    """The periodic task wraps the cleanup and returns counters."""
    _image(5)
    outcome = cleanup_editor_images()
    assert outcome["deleted"] == 1
    assert outcome["aborted"] == ""
