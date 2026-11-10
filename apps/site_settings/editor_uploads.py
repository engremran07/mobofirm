"""Store images uploaded from the editor: ownership, quota, EXIF-free, random names."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.contrib.auth.models import PermissionsMixin
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.editor.spec import CONTEXT_LABELS, get_profile
from apps.core.editor.uploads import process_image

from .editor_models import EditorImage

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

logger = logging.getLogger(__name__)

STAFF_CONTEXTS = frozenset({"page", "announcement", "admin_default", "firmware_notes"})
BLOG_PERMISSIONS = ("blog.add_post", "blog.change_post")
TOTAL_BYTES_PER_USER = 200 * 1024 * 1024
QUOTA_WINDOW = timedelta(hours=24)


class UploadNotAllowedError(PermissionError):
    """The editor, context or user may not upload images."""


class QuotaExceededError(Exception):
    """The user used up the daily image count or the total storage allowance."""


def _is_staff(user: AbstractBaseUser) -> bool:
    return bool(getattr(user, "is_staff", False))


def _has_blog_permission(user: AbstractBaseUser) -> bool:
    return isinstance(user, PermissionsMixin) and any(
        user.has_perm(perm) for perm in BLOG_PERMISSIONS
    )


def may_upload(user: AbstractBaseUser, context: str) -> bool:
    """Whether ``user`` may upload images for ``context``."""
    if not user.is_authenticated or not user.is_active:
        return False
    is_staff = _is_staff(user)
    if context in STAFF_CONTEXTS:
        return is_staff
    if context == "blog_post":
        return is_staff or _has_blog_permission(user)
    return True


def _enforce_quota(user: AbstractBaseUser, daily_limit: int) -> None:
    if _is_staff(user):
        return
    mine = EditorImage.objects.filter(owner_id=user.pk)
    since = timezone.now() - QUOTA_WINDOW
    if mine.filter(created_at__gte=since).count() >= daily_limit:
        raise QuotaExceededError("Daily image upload limit reached.")
    used = mine.aggregate(total=Sum("size_bytes"))["total"] or 0
    if used >= TOTAL_BYTES_PER_USER:
        raise QuotaExceededError("Image storage allowance reached.")


def store_editor_image(
    user: AbstractBaseUser, context: str, upload: UploadedFile
) -> EditorImage:
    """Validate, re-encode and store ``upload``; raises on any policy violation."""
    spec = get_profile(context)
    if context not in CONTEXT_LABELS or not (
        spec.enabled and spec.upload_available and may_upload(user, context)
    ):
        raise UploadNotAllowedError("Image uploads are not available here.")
    processed = process_image(upload, spec.upload.max_kb)
    with transaction.atomic():
        _enforce_quota(user, spec.upload.daily_limit)
        image = EditorImage(
            owner_id=user.pk,
            context=context,
            width=processed.width,
            height=processed.height,
            size_bytes=len(processed.content),
        )
        image.file.save(
            f"upload{processed.extension}", ContentFile(processed.content), save=False
        )
        image.save()
    logger.info(
        "editor_image_stored",
        extra={"image_id": image.pk, "context": context, "bytes": image.size_bytes},
    )
    return image
