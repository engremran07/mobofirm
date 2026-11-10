"""Models behind access-type changes of approved firmware.

* :class:`AccessChangeLog` is the append-only trail of every change (who, when, from,
  to, price, reason), written by :mod:`apps.firmwares.access_changes` only.
* :class:`AccessChangeRequest` is an uploader's / approved seller's request that staff
  approve or reject.
* :class:`AutoFeatureSettings` is the admin-edited configuration of the automatic
  promotion of very popular free files to featured.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from solo.models import SingletonModel

from .models import AccessTier


def price_field(**kwargs: Any) -> models.DecimalField:
    """A nullable USD amount column."""
    return models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, **kwargs
    )


class AccessActor(models.TextChoices):
    """Who made an access change."""

    STAFF = "staff", "Staff"
    SELLER = "seller", "Seller"
    SYSTEM = "system", "System (automatic)"


class AccessChangeSource(models.TextChoices):
    """The route an access change took."""

    ADMIN = "admin", "Admin form"
    DIALOG = "dialog", "Dialog"
    BULK = "bulk", "Bulk action"
    REQUEST = "request", "Approved request"
    AUTO = "auto", "Automatic popularity rule"
    REVERT = "revert", "Reverted"
    APPROVAL = "approval", "Upload approval"


class FirmwareRef(models.Model):
    """Generic reference to one typed firmware row (shared by the models below)."""

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.UUIDField(db_index=True)
    firmware = GenericForeignKey("content_type", "object_id")
    firmware_label = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="File name at the time, kept if the file is deleted later",
    )

    class Meta:
        abstract = True


class AccessChangeRequest(FirmwareRef):
    """A request to move an approved file to another access type (staff decide)."""

    class Status(models.TextChoices):
        """Where the request stands."""

        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="access_change_requests",
    )
    from_tier = models.CharField(max_length=16, choices=AccessTier.choices)
    to_tier = models.CharField(max_length=16, choices=AccessTier.choices)
    proposed_price = price_field(
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Price proposed by the requester in USD (paid files only)",
    )
    reason = models.TextField(max_length=1000)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    approved_price = price_field(help_text="Price staff approved (paid requests)")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, default="", max_length=1000)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Access change request"
        verbose_name_plural = "Access change requests"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=Q(status="pending"),
                name="firmware_one_pending_access_request",
                violation_error_message="This file already has a pending request.",
            ),
            models.CheckConstraint(
                condition=Q(proposed_price__isnull=True) | Q(to_tier="paid"),
                name="access_request_price_only_paid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.firmware_label}: {self.from_tier} → {self.to_tier}"

    @property
    def is_pending(self) -> bool:
        """Whether staff still have to decide."""
        return self.status == self.Status.PENDING


class AccessChangeLog(FirmwareRef):
    """Append-only record of one access-type or price change of a firmware file."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    actor_kind = models.CharField(max_length=10, choices=AccessActor.choices)
    source = models.CharField(max_length=10, choices=AccessChangeSource.choices)
    from_tier = models.CharField(max_length=16, choices=AccessTier.choices)
    to_tier = models.CharField(max_length=16, choices=AccessTier.choices)
    from_price = price_field()
    to_price = price_field()
    reason = models.CharField(max_length=1000, blank=True, default="")
    request = models.ForeignKey(
        AccessChangeRequest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="log_entries",
    )
    reverts = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reverted_by",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Access change"
        verbose_name_plural = "Access change history"
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["content_type", "object_id", "-created_at"],
                name="accesschange_file_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.firmware_label}: {self.from_tier} → {self.to_tier}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Rows are only ever inserted."""
        if not self._state.adding:
            raise RuntimeError("The access change history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """History is never deleted."""
        raise RuntimeError("The access change history is append-only.")


class AutoFeatureSettings(SingletonModel):
    """Admin-edited thresholds of the automatic 'popular → featured' promotion."""

    enabled = models.BooleanField(
        default=False,
        help_text="Master switch. When off the scheduled task does nothing.",
    )
    dry_run = models.BooleanField(
        default=True,
        help_text="Only report what would be promoted; change nothing.",
    )
    window_days = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        help_text="Downloads are counted over this many days.",
    )
    min_downloads = models.PositiveIntegerField(
        default=200, help_text="Completed downloads needed within the window."
    )
    min_unique_downloaders = models.PositiveIntegerField(
        default=50,
        help_text="Different people (accounts or visitors) who downloaded it.",
    )
    velocity_days = models.PositiveIntegerField(
        default=7,
        validators=[MinValueValidator(1)],
        help_text="Recent period used to measure the download velocity.",
    )
    min_velocity = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal(0))],
        help_text="Downloads per day over the velocity period.",
    )
    cooldown_days = models.PositiveIntegerField(
        default=14,
        help_text="A file whose access changed in this many days is left alone.",
    )
    max_promotions_per_run = models.PositiveIntegerField(
        default=5, help_text="Most files promoted by one run (0 = none)."
    )
    notify_uploader = models.BooleanField(
        default=True, help_text="Tell the uploader when their file is promoted."
    )
    last_run_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_run_report = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        verbose_name = "Auto-feature settings"
        verbose_name_plural = "Auto-feature settings"

    def __str__(self) -> str:
        return "Auto-feature settings"
