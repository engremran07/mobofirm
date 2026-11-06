from __future__ import annotations

import secrets
import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.users.models import TimestampedModel

from . import conf
from .storage import get_private_storage

MAX_TAG_LENGTH = 40


def generate_share_token() -> str:
    return secrets.token_urlsafe(32)


def stored_file_path(instance: FileItem, filename: str) -> str:
    """Server-chosen path: never derived from the client-supplied file name."""
    return f"{instance.owner_id}/{uuid.uuid4().hex}"


def version_file_path(instance: FileVersion, filename: str) -> str:
    return f"{instance.file_item.owner_id}/{uuid.uuid4().hex}"


def thumbnail_path(instance: FileItem, filename: str) -> str:
    return f"thumbnails/{instance.owner_id}/{uuid.uuid4().hex}.jpg"


class FileType(models.TextChoices):
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    DOCUMENT = "document", "Document"
    ARCHIVE = "archive", "Archive"
    OTHER = "other", "Other"


class Folder(TimestampedModel):
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filemanager_folders",
    )
    description = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = "Folder"
        verbose_name_plural = "Folders"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["owner", "parent", "name"],
                condition=Q(parent__isnull=False),
                name="filemanager_unique_child_folder_name",
            ),
            models.UniqueConstraint(
                fields=["owner", "name"],
                condition=Q(parent__isnull=True),
                name="filemanager_unique_root_folder_name",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def get_ancestors(self) -> list[Folder]:
        ancestors: list[Folder] = []
        current = self.parent
        while current is not None:
            ancestors.insert(0, current)
            current = current.parent
        return ancestors


class FileTag(TimestampedModel):
    """A private label an owner attaches to their own files (never a public topic)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filemanager_tags",
    )
    name = models.CharField(max_length=MAX_TAG_LENGTH)
    slug = models.SlugField(max_length=MAX_TAG_LENGTH)

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = "File Tag"
        verbose_name_plural = "File Tags"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["owner", "slug"], name="filemanager_unique_tag_per_owner"
            )
        ]

    def __str__(self) -> str:
        return self.name


class FileItem(TimestampedModel):
    name = models.CharField(max_length=255)
    file = models.FileField(
        upload_to=stored_file_path, storage=get_private_storage, max_length=255
    )
    thumbnail = models.FileField(
        upload_to=thumbnail_path,
        storage=get_private_storage,
        max_length=255,
        blank=True,
    )
    tags = models.ManyToManyField(FileTag, blank=True, related_name="files")
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="files",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filemanager_files",
    )
    file_type = models.CharField(
        max_length=20, choices=FileType.choices, default=FileType.OTHER
    )
    mime_type = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    download_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "File"
        verbose_name_plural = "Files"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["folder", "is_active"]),
            models.Index(fields=["owner", "file_type"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


class FileVersion(TimestampedModel):
    """An earlier version of a file; the current version lives on the FileItem."""

    file_item = models.ForeignKey(
        FileItem, on_delete=models.CASCADE, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    file = models.FileField(
        upload_to=version_file_path, storage=get_private_storage, max_length=255
    )
    size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="filemanager_versions",
        help_text="Who replaced this version.",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-version_number"]
        verbose_name = "File Version"
        verbose_name_plural = "File Versions"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["file_item", "version_number"],
                name="filemanager_unique_version_number",
            )
        ]

    def __str__(self) -> str:
        return f"{self.file_item.name} v{self.version_number}"


class FolderPermission(TimestampedModel):
    folder = models.ForeignKey(
        Folder, on_delete=models.CASCADE, related_name="permissions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="filemanager_folder_permissions",
    )
    group = models.ForeignKey(
        "auth.Group",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="filemanager_folder_permissions",
    )
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_share = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Folder Permission"
        verbose_name_plural = "Folder Permissions"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | Q(group__isnull=False),
                name="filemanager_permission_user_or_group",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.folder.name} → {self.user or self.group}"


class FileShare(TimestampedModel):
    file_item = models.ForeignKey(
        FileItem, on_delete=models.CASCADE, related_name="shares"
    )
    token = models.CharField(
        max_length=64, unique=True, default=generate_share_token, editable=False
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filemanager_shares",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    max_downloads = models.PositiveIntegerField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    password_hash = models.CharField(max_length=255, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "File Share"
        verbose_name_plural = "File Shares"

    def __str__(self) -> str:
        return f"Share of {self.file_item.name}"


class StorageUsage(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filemanager_storage_usage",
    )
    used_bytes = models.BigIntegerField(default=0)
    quota_bytes = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Per-user override; empty uses FILEMANAGER_USER_QUOTA_BYTES.",
    )

    class Meta:
        verbose_name = "Storage Usage"
        verbose_name_plural = "Storage Usages"

    def __str__(self) -> str:
        return f"{self.user}: {self.used_bytes} / {self.effective_quota} bytes"

    @property
    def effective_quota(self) -> int:
        if self.quota_bytes is not None:
            return self.quota_bytes
        return conf.user_quota_bytes()

    @property
    def percent_used(self) -> float:
        quota = self.effective_quota
        return 100.0 if quota <= 0 else min(100.0, self.used_bytes / quota * 100)
