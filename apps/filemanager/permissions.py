"""Folder/file access rules. Owners have full access; others need a FolderPermission."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet

from .models import FileItem, Folder, FolderPermission

ACTION_FLAGS = {
    "read": "can_read",
    "write": "can_write",
    "delete": "can_delete",
    "share": "can_share",
}


def _grants(user: Any, action: str) -> QuerySet[FolderPermission]:
    if not getattr(user, "is_authenticated", False):
        return FolderPermission.objects.none()
    return FolderPermission.objects.filter(
        Q(user=user) | Q(group__in=user.groups.all()), **{ACTION_FLAGS[action]: True}
    )


def can_access_folder(user: Any, folder: Folder | None, action: str) -> bool:
    """Root (``None``) belongs to its owner only; owners always pass."""
    if not getattr(user, "is_authenticated", False):
        return False
    if folder is None:
        return True
    if folder.owner_id == user.pk:
        return True
    return _grants(user, action).filter(folder=folder).exists()


def can_access_file(user: Any, item: FileItem, action: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if item.owner_id == user.pk:
        return True
    return item.folder is not None and can_access_folder(user, item.folder, action)


def readable_folders(user: Any) -> QuerySet[Folder]:
    granted = _grants(user, "read").values("folder")
    return Folder.objects.filter(Q(owner=user) | Q(pk__in=granted))


def readable_files(user: Any) -> QuerySet[FileItem]:
    granted = _grants(user, "read").values("folder")
    return FileItem.objects.filter(is_active=True).filter(
        Q(owner=user) | Q(folder__in=granted)
    )
