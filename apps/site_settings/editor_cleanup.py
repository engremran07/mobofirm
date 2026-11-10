"""Delete editor images that no stored content references any more."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from apps.core.editor.references import RICH_TEXT_FIELDS

from .editor_models import UPLOAD_PREFIX, EditorImage

logger = logging.getLogger(__name__)

GRACE_PERIOD = timedelta(hours=24)
_IMAGE_PATH = re.compile(rf"{UPLOAD_PREFIX}/\d{{4}}/\d{{2}}/[0-9a-f]{{32}}\.[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Outcome of one cleanup run."""

    deleted: int
    kept: int
    aborted: str = ""


class ReferenceScanError(LookupError):
    """A registered rich-text field could not be resolved, so scanning is unsafe."""


def _field_values(spec: str) -> Iterable[str]:
    app_label, model_name, field = spec.split(".")
    if app_label not in django_apps.app_configs:
        return ()
    try:
        model: type[Model] = django_apps.get_model(app_label, model_name)
        model._meta.get_field(field)
    except (LookupError, FieldDoesNotExist) as error:
        raise ReferenceScanError(spec) from error
    queryset = model._meta.base_manager.filter(
        **{f"{field}__contains": f"{UPLOAD_PREFIX}/"}
    )
    return queryset.values_list(field, flat=True).iterator()


def referenced_paths() -> set[str]:
    """Every editor image path mentioned in any registered rich-text field."""
    found: set[str] = set()
    for spec in RICH_TEXT_FIELDS:
        for value in _field_values(spec):
            found.update(_IMAGE_PATH.findall(str(value or "")))
    return found


def cleanup_orphan_images(
    *, grace: timedelta = GRACE_PERIOD, dry_run: bool = False
) -> CleanupResult:
    """Remove images older than ``grace`` that no content references.

    Fails closed: if any registered field cannot be scanned nothing is deleted.
    """
    cutoff = timezone.now() - grace
    candidates = list(EditorImage.objects.filter(created_at__lt=cutoff))
    if not candidates:
        return CleanupResult(0, 0)
    try:
        referenced = referenced_paths()
    except ReferenceScanError as error:
        logger.error("editor_cleanup_aborted", extra={"field": str(error)})
        return CleanupResult(0, len(candidates), aborted=str(error))
    deleted = 0
    for image in candidates:
        if image.file.name in referenced:
            continue
        deleted += 1
        if not dry_run:
            image.delete()
    logger.info(
        "editor_cleanup_done",
        extra={"deleted": deleted, "dry_run": dry_run, "scanned": len(candidates)},
    )
    return CleanupResult(deleted, len(candidates) - deleted)


@receiver(post_delete, sender=EditorImage, dispatch_uid="editor_image_file_cleanup")
def delete_image_file(
    sender: type[EditorImage], instance: EditorImage, **_kwargs: object
) -> None:
    """Remove the stored file together with its row."""
    del sender
    if instance.file:
        instance.file.delete(save=False)
