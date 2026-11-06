from __future__ import annotations

import uuid
from typing import ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from . import providers


class FirmwareStorageLocation(models.Model):
    """Where the bytes of one typed firmware record live.

    The firmware row stays the owner of classification and review state; this row
    only records provider, location, integrity and verification state.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending verification"
        AVAILABLE = "available", "Available"
        MISSING = "missing", "Missing"
        MISMATCH = "mismatch", "Checksum mismatch"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    firmware = GenericForeignKey("content_type", "object_id")

    provider = models.CharField(max_length=20, choices=providers.PROVIDER_CHOICES)
    relative_path = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Path below FIRMWARE_STORAGE_ROOT (local provider).",
    )
    external_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text="https URL on an allow-listed host (external link provider).",
    )
    sha256 = models.CharField(max_length=64, blank=True, default="")
    size_bytes = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Firmware storage location"
        verbose_name_plural = "Firmware storage locations"
        ordering: ClassVar[list[str]] = ["-updated_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "provider"],
                name="storage_one_location_per_provider",
            ),
            models.CheckConstraint(
                condition=(~Q(provider="local") | ~Q(relative_path="")),
                name="storage_local_requires_path",
            ),
            models.CheckConstraint(
                condition=(~Q(provider="external_link") | ~Q(external_url="")),
                name="storage_external_requires_url",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["content_type", "object_id"])
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} location for {self.object_id}"

    def clean(self) -> None:
        super().clean()
        if self.provider == providers.LOCAL:
            try:
                providers.resolve_local_path(self.relative_path)
            except providers.StoragePathError as exc:
                raise ValidationError({"relative_path": str(exc)}) from exc
        elif self.provider == providers.EXTERNAL_LINK:
            try:
                providers.validate_external_url(self.external_url)
            except providers.StorageUrlError as exc:
                raise ValidationError({"external_url": str(exc)}) from exc
        elif not providers.provider_is_usable(self.provider):
            raise ValidationError(
                {"provider": "This provider is disabled; see the storage status page."}
            )
