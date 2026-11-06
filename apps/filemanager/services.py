"""File manager business logic: uploads, quotas, folders, deletion and share links."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, Max, Q, Sum
from django.utils import timezone
from django.utils.text import slugify

from . import permissions
from .models import (
    MAX_TAG_LENGTH,
    FileItem,
    FileShare,
    FileTag,
    FileType,
    FileVersion,
    Folder,
    StorageUsage,
)
from .thumbnails import make_thumbnail
from .validation import UploadRejected, sanitize_filename, validate_upload

UPLOADS_PER_MINUTE = 30
SHARE_MAX_FAILED_ATTEMPTS = 5
SHARE_LOCK_MINUTES = 15
MAX_FOLDER_NAME_LENGTH = 255
MAX_VERSIONS_PER_FILE = 10
MAX_TAGS_PER_FILE = 10
TRASH_RETENTION_DAYS = 30


class FileManagerError(ValueError):
    """User-facing failure of a file manager operation."""


class QuotaExceeded(FileManagerError):
    pass


class PermissionDenied(FileManagerError):
    pass


class ShareUnavailable(FileManagerError):
    """The share does not exist, expired, was revoked or is exhausted."""


class ShareLocked(ShareUnavailable):
    """Too many wrong passwords; try again later."""


class WrongSharePassword(FileManagerError):
    pass


def get_usage(user: Any) -> StorageUsage:
    usage, _ = StorageUsage.objects.get_or_create(user=user)
    return usage


def _check_upload_rate(user: Any) -> None:
    key = f"filemanager:uploads:{user.pk}"
    cache.add(key, 0, timeout=60)
    if cache.incr(key) > UPLOADS_PER_MINUTE:
        raise FileManagerError("Too many uploads; wait a minute and try again.")


def clean_folder_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > MAX_FOLDER_NAME_LENGTH or "/" in name or "\\" in name:
        raise FileManagerError("Enter a folder name without slashes.")
    if name in {".", ".."}:
        raise FileManagerError("Enter a valid folder name.")
    return name


def create_folder(user: Any, name: str, parent: Folder | None) -> Folder:
    if not permissions.can_access_folder(user, parent, "write"):
        raise PermissionDenied("You cannot create folders here.")
    owner = parent.owner if parent else user
    try:
        with transaction.atomic():
            return Folder.objects.create(
                name=clean_folder_name(name), parent=parent, owner=owner
            )
    except IntegrityError as exc:
        raise FileManagerError("A folder with that name already exists.") from exc


def _attach_thumbnail(item: FileItem, source: UploadedFile) -> None:
    """Store a preview for image files; a file without one simply shows an icon."""
    if item.file_type != FileType.IMAGE:
        return
    data = make_thumbnail(source)
    if data is not None:
        item.thumbnail.save("thumbnail.jpg", ContentFile(data), save=False)


def total_bytes(item: FileItem) -> int:
    """Bytes the item occupies in its owner's quota: current file plus all versions."""
    versions = item.versions.aggregate(total=Sum("size"))["total"] or 0
    return item.size + versions


def store_upload(user: Any, uploaded: UploadedFile, folder: Folder | None) -> FileItem:
    """Validate, enforce the quota under a row lock and persist one upload."""
    if not permissions.can_access_folder(user, folder, "write"):
        raise PermissionDenied("You cannot upload to this folder.")
    _check_upload_rate(user)
    try:
        validated = validate_upload(uploaded)
    except UploadRejected as exc:
        raise FileManagerError(str(exc)) from exc

    owner = folder.owner if folder else user
    get_usage(owner)
    item = FileItem(
        name=validated.name,
        owner=owner,
        folder=folder,
        file_type=validated.file_type,
        mime_type=validated.mime_type,
        size=validated.size,
        sha256=validated.sha256,
    )
    with transaction.atomic():
        usage = StorageUsage.objects.select_for_update().get(user=owner)
        if usage.used_bytes + validated.size > usage.effective_quota:
            raise QuotaExceeded("Not enough storage quota for this file.")
        item.file.save(validated.name, uploaded, save=False)
        _attach_thumbnail(item, uploaded)
        try:
            item.save()
        except Exception:
            item.file.delete(save=False)
            item.thumbnail.delete(save=False)
            raise
        StorageUsage.objects.filter(pk=usage.pk).update(
            used_bytes=F("used_bytes") + validated.size
        )
    return item


def rename_file(user: Any, item: FileItem, new_name: str) -> FileItem:
    if not permissions.can_access_file(user, item, "write"):
        raise PermissionDenied("You cannot rename this file.")
    try:
        clean = sanitize_filename(new_name)
    except UploadRejected as exc:
        raise FileManagerError(str(exc)) from exc
    if clean.rsplit(".", 1)[-1].lower() != item.extension:
        raise FileManagerError("The file extension cannot be changed.")
    item.name = clean
    item.save(update_fields=["name", "updated_at"])
    return item


@transaction.atomic
def soft_delete_file(user: Any, item: FileItem) -> FileItem:
    """Hide the file, release its quota, revoke shares; bytes are purged later."""
    if not permissions.can_access_file(user, item, "delete"):
        raise PermissionDenied("You cannot delete this file.")
    locked = FileItem.objects.select_for_update().get(pk=item.pk)
    if not locked.is_active:
        return locked
    locked.is_active = False
    locked.deleted_at = timezone.now()
    locked.save(update_fields=["is_active", "deleted_at", "updated_at"])
    StorageUsage.objects.filter(user=locked.owner).update(
        used_bytes=F("used_bytes") - total_bytes(locked)
    )
    locked.shares.update(is_active=False)
    return locked


def _delete_bytes(item: FileItem) -> None:
    """Remove every stored byte that belongs to the item (current, versions, preview)."""
    for version in item.versions.all():
        version.file.delete(save=False)
    item.file.delete(save=False)
    item.thumbnail.delete(save=False)


def purge_deleted_files(older_than_days: int = TRASH_RETENTION_DAYS) -> int:
    """Physically remove files soft-deleted for longer than the retention window."""
    cutoff = timezone.now() - timedelta(days=older_than_days)
    purged = 0
    for item in FileItem.objects.filter(is_active=False, deleted_at__lt=cutoff):
        _delete_bytes(item)
        item.delete()
        purged += 1
    return purged


def recalculate_usage(user: Any) -> StorageUsage:
    """Recompute used bytes from active files and their versions (the source of truth)."""
    with transaction.atomic():
        usage = get_usage(user)
        usage = StorageUsage.objects.select_for_update().get(pk=usage.pk)
        active = FileItem.objects.filter(owner=user, is_active=True)
        current = active.aggregate(total=Sum("size"))["total"] or 0
        versions = (
            FileVersion.objects.filter(file_item__in=active).aggregate(
                total=Sum("size")
            )["total"]
            or 0
        )
        usage.used_bytes = current + versions
        usage.save(update_fields=["used_bytes", "updated_at"])
    return usage


def rename_folder(user: Any, folder: Folder, new_name: str) -> Folder:
    if not permissions.can_access_folder(user, folder, "write"):
        raise PermissionDenied("You cannot rename this folder.")
    folder.name = clean_folder_name(new_name)
    try:
        with transaction.atomic():
            folder.save(update_fields=["name", "updated_at"])
    except IntegrityError as exc:
        raise FileManagerError("A folder with that name already exists.") from exc
    return folder


@transaction.atomic
def delete_folder(user: Any, folder: Folder) -> None:
    """Delete an empty folder. Files in the trash move to the owner's root first."""
    if not permissions.can_access_folder(user, folder, "delete"):
        raise PermissionDenied("You cannot delete this folder.")
    if folder.children.exists() or folder.files.filter(is_active=True).exists():
        raise FileManagerError("Only empty folders can be deleted.")
    folder.files.filter(is_active=False).update(folder=None)
    folder.delete()


def folder_path_choices(owner: Any) -> list[tuple[int, str]]:
    """Every folder of ``owner`` as ``(id, "Parent / Child")`` for move menus."""
    rows = {
        folder_id: (name, parent_id)
        for folder_id, name, parent_id in Folder.objects.filter(
            owner=owner
        ).values_list("id", "name", "parent_id")
    }

    def path(folder_id: int) -> str:
        names: list[str] = []
        seen: set[int] = set()
        current: int | None = folder_id
        while current is not None and current in rows and current not in seen:
            seen.add(current)
            name, parent_id = rows[current]
            names.append(name)
            current = parent_id
        return " / ".join(reversed(names))

    return sorted(
        ((folder_id, path(folder_id)) for folder_id in rows),
        key=lambda c: c[1].lower(),
    )


def move_file(user: Any, item: FileItem, destination: Folder | None) -> FileItem:
    """Move a file between folders of the same owner."""
    if not permissions.can_access_file(user, item, "write"):
        raise PermissionDenied("You cannot move this file.")
    if destination is None:
        if user.pk != item.owner_id:
            raise PermissionDenied("Only the owner can move a file to the top level.")
    elif not permissions.can_access_folder(user, destination, "write"):
        raise PermissionDenied("You cannot move files into that folder.")
    elif destination.owner_id != item.owner_id:
        raise FileManagerError("Files can only move between one owner's folders.")
    item.folder = destination
    item.save(update_fields=["folder", "updated_at"])
    return item


# --- Trash -----------------------------------------------------------------


def trashed_files(user: Any):
    return FileItem.objects.filter(owner=user, is_active=False).order_by("-deleted_at")


@transaction.atomic
def restore_file(user: Any, item: FileItem) -> FileItem:
    """Bring a trashed file back, re-checking the quota it will occupy again."""
    if not permissions.can_access_file(user, item, "delete"):
        raise PermissionDenied("You cannot restore this file.")
    locked = FileItem.objects.select_for_update().get(pk=item.pk)
    if locked.is_active:
        return locked
    get_usage(locked.owner)
    usage = StorageUsage.objects.select_for_update().get(user=locked.owner)
    size = total_bytes(locked)
    if usage.used_bytes + size > usage.effective_quota:
        raise QuotaExceeded("Not enough storage quota to restore this file.")
    locked.is_active = True
    locked.deleted_at = None
    locked.save(update_fields=["is_active", "deleted_at", "updated_at"])
    StorageUsage.objects.filter(pk=usage.pk).update(used_bytes=F("used_bytes") + size)
    return locked


@transaction.atomic
def purge_file(user: Any, item: FileItem) -> None:
    """Permanently delete a file that is already in the trash."""
    if not permissions.can_access_file(user, item, "delete"):
        raise PermissionDenied("You cannot delete this file.")
    locked = FileItem.objects.select_for_update().get(pk=item.pk)
    if locked.is_active:
        raise FileManagerError(
            "Move the file to the trash before deleting it for good."
        )
    _delete_bytes(locked)
    locked.delete()


def empty_trash(user: Any) -> int:
    count = 0
    for item in trashed_files(user):
        purge_file(user, item)
        count += 1
    return count


# --- Tags ------------------------------------------------------------------


def parse_tag_names(raw: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(";", ",").split(","):
        name = " ".join(part.split())
        if not name:
            continue
        if len(name) > MAX_TAG_LENGTH:
            raise FileManagerError(f"Tags can be at most {MAX_TAG_LENGTH} characters.")
        slug = slugify(name)
        if not slug:
            raise FileManagerError("Tags need at least one letter or number.")
        if slug not in seen:
            seen.add(slug)
            names.append(name)
    if len(names) > MAX_TAGS_PER_FILE:
        raise FileManagerError(f"A file can have at most {MAX_TAGS_PER_FILE} tags.")
    return names


@transaction.atomic
def set_file_tags(user: Any, item: FileItem, raw: str) -> list[FileTag]:
    """Replace the file's tags. Tags belong to the file owner and are never public."""
    if not permissions.can_access_file(user, item, "write"):
        raise PermissionDenied("You cannot tag this file.")
    tags = []
    for name in parse_tag_names(raw):
        tag, _ = FileTag.objects.get_or_create(
            owner_id=item.owner_id, slug=slugify(name), defaults={"name": name}
        )
        tags.append(tag)
    item.tags.set(tags)
    FileTag.objects.filter(owner_id=item.owner_id, files__isnull=True).delete()
    return tags


# --- Versions --------------------------------------------------------------


def _next_version_number(item: FileItem) -> int:
    latest = item.versions.aggregate(latest=Max("version_number"))["latest"]
    return (latest or 0) + 1


def _replace_thumbnail(item: FileItem, source: Any) -> None:
    old = item.thumbnail.name
    item.thumbnail = ""
    if item.file_type == FileType.IMAGE:
        data = make_thumbnail(source)
        if data is not None:
            item.thumbnail.save("thumbnail.jpg", ContentFile(data), save=False)
    if old and old != item.thumbnail.name:
        item.thumbnail.storage.delete(old)


def add_version(user: Any, item: FileItem, uploaded: UploadedFile) -> FileItem:
    """Replace the current content; the previous content is kept as a version."""
    if not item.is_active or not permissions.can_access_file(user, item, "write"):
        raise PermissionDenied("You cannot upload a new version of this file.")
    _check_upload_rate(user)
    try:
        validated = validate_upload(uploaded)
    except UploadRejected as exc:
        raise FileManagerError(str(exc)) from exc
    if validated.extension != item.extension:
        raise FileManagerError(
            f"A new version must be a .{item.extension or 'same-type'} file."
        )

    get_usage(item.owner)
    stored_name = ""
    try:
        with transaction.atomic():
            locked = FileItem.objects.select_for_update().get(pk=item.pk)
            usage = StorageUsage.objects.select_for_update().get(user=locked.owner)
            if locked.versions.count() >= MAX_VERSIONS_PER_FILE:
                raise FileManagerError(
                    "This file has reached its version limit; delete an old version first."
                )
            if usage.used_bytes + validated.size > usage.effective_quota:
                raise QuotaExceeded("Not enough storage quota for this version.")
            FileVersion.objects.create(
                file_item=locked,
                version_number=_next_version_number(locked),
                file=locked.file.name,
                size=locked.size,
                sha256=locked.sha256,
                mime_type=locked.mime_type,
                created_by=user,
            )
            locked.file.save(validated.name, uploaded, save=False)
            stored_name = locked.file.name or ""
            locked.size = validated.size
            locked.sha256 = validated.sha256
            locked.mime_type = validated.mime_type
            _replace_thumbnail(locked, uploaded)
            locked.save()
            StorageUsage.objects.filter(pk=usage.pk).update(
                used_bytes=F("used_bytes") + validated.size
            )
    except (DatabaseError, OSError):
        if stored_name:
            item.file.storage.delete(stored_name)
        raise
    return locked


@transaction.atomic
def restore_version(user: Any, item: FileItem, version: FileVersion) -> FileItem:
    """Make an earlier version current; the content it replaces becomes a version."""
    if not item.is_active or not permissions.can_access_file(user, item, "write"):
        raise PermissionDenied("You cannot restore versions of this file.")
    locked = FileItem.objects.select_for_update().get(pk=item.pk)
    chosen = (
        FileVersion.objects.select_for_update()
        .filter(pk=version.pk, file_item=locked)
        .first()
    )
    if chosen is None:
        raise FileManagerError("That version no longer exists.")
    FileVersion.objects.create(
        file_item=locked,
        version_number=_next_version_number(locked),
        file=locked.file.name,
        size=locked.size,
        sha256=locked.sha256,
        mime_type=locked.mime_type,
        created_by=user,
    )
    locked.file = chosen.file.name
    locked.size = chosen.size
    locked.sha256 = chosen.sha256
    locked.mime_type = chosen.mime_type
    chosen.delete()
    with locked.file.open("rb") as handle:
        _replace_thumbnail(locked, handle)
    locked.save()
    return locked


@transaction.atomic
def delete_version(user: Any, item: FileItem, version: FileVersion) -> None:
    if not permissions.can_access_file(user, item, "delete"):
        raise PermissionDenied("You cannot delete versions of this file.")
    locked = FileItem.objects.select_for_update().get(pk=item.pk)
    chosen = FileVersion.objects.filter(pk=version.pk, file_item=locked).first()
    if chosen is None:
        raise FileManagerError("That version no longer exists.")
    size = chosen.size
    chosen.file.delete(save=False)
    chosen.delete()
    if locked.is_active:
        StorageUsage.objects.filter(user=locked.owner).update(
            used_bytes=F("used_bytes") - size
        )


# --- Share links -----------------------------------------------------------


def create_share(
    user: Any,
    item: FileItem,
    *,
    expires_hours: int | None = None,
    max_downloads: int | None = None,
    password: str = "",
) -> FileShare:
    if not item.is_active or not permissions.can_access_file(user, item, "share"):
        raise PermissionDenied("You cannot share this file.")
    if expires_hours is not None and not 1 <= expires_hours <= 24 * 365:
        raise FileManagerError("Expiry must be between 1 hour and 365 days.")
    if max_downloads is not None and max_downloads < 1:
        raise FileManagerError("Maximum downloads must be at least 1.")
    return FileShare.objects.create(
        file_item=item,
        created_by=user,
        expires_at=(
            timezone.now() + timedelta(hours=expires_hours) if expires_hours else None
        ),
        max_downloads=max_downloads,
        password_hash=make_password(password) if password else "",
    )


def revoke_share(user: Any, share: FileShare) -> FileShare:
    if share.created_by_id != user.pk and share.file_item.owner_id != user.pk:
        raise PermissionDenied("You cannot revoke this share.")
    share.is_active = False
    share.save(update_fields=["is_active", "updated_at"])
    return share


def user_shares(user: Any):
    """Live share links created by ``user`` for files that are not in the trash."""
    return (
        FileShare.objects.filter(
            created_by=user, is_active=True, file_item__is_active=True
        )
        .select_related("file_item")
        .order_by("-created_at")
    )


def authorize_share(token: str, password: str = "") -> FileShare:
    """Return the share if the link is usable and the password (if any) is right.

    Wrong passwords are counted in the database; five failures lock the link for
    fifteen minutes so the password cannot be brute-forced.
    """
    now = timezone.now()
    share = (
        FileShare.objects.select_related("file_item")
        .filter(token=token, is_active=True, file_item__is_active=True)
        .first()
    )
    if share is None or (share.expires_at and share.expires_at <= now):
        raise ShareUnavailable("This link is not available.")
    if share.max_downloads is not None and share.download_count >= share.max_downloads:
        raise ShareUnavailable("This link has reached its download limit.")
    if share.locked_until and share.locked_until > now:
        raise ShareLocked("Too many attempts. Try again later.")
    if not share.password_hash:
        return share
    if check_password(password, share.password_hash):
        if share.failed_attempts:
            FileShare.objects.filter(pk=share.pk).update(failed_attempts=0)
        return share

    FileShare.objects.filter(pk=share.pk).update(
        failed_attempts=F("failed_attempts") + 1
    )
    FileShare.objects.filter(
        pk=share.pk, failed_attempts__gte=SHARE_MAX_FAILED_ATTEMPTS
    ).update(
        failed_attempts=0, locked_until=now + timedelta(minutes=SHARE_LOCK_MINUTES)
    )
    raise WrongSharePassword("The password is not correct.")


def consume_share(share: FileShare) -> bool:
    """Atomically count one download; False if the limit was just reached."""
    updated = (
        FileShare.objects.filter(pk=share.pk, is_active=True)
        .filter(
            Q(max_downloads__isnull=True) | Q(download_count__lt=F("max_downloads"))
        )
        .update(download_count=F("download_count") + 1)
    )
    if updated:
        FileItem.objects.filter(pk=share.file_item_id).update(
            download_count=F("download_count") + 1
        )
    return bool(updated)


def expire_shares() -> int:
    """Deactivate shares past their expiry (idempotent)."""
    return FileShare.objects.filter(
        is_active=True, expires_at__lt=timezone.now()
    ).update(is_active=False)
