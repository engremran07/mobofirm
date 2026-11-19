"""Ad unlock tickets: the shop's record of a file unlocked by watching rewarded ads.

An over-allowance account on a package that offers it can unlock ONE free or featured
file by watching N rewarded ads. The ticket is the state machine of that unlock; the
watches themselves are ``ads.RewardedAdView`` rows (server-validated by ``apps.ads``),
referenced here only by their numeric id so the two apps share no foreign key.

Lifecycle: ``open`` (counting ads) -> ``ready`` (all ads watched, one download may use
it until ``expires_at``) -> ``redeemed`` (the download started). ``expired`` and
``cancelled`` end an unlock that was never used; the same user and file can start a new
cycle later (``cycle`` counts them), so there is exactly one ticket per user and file.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from .models import PackageTier


class AdUnlockTicket(models.Model):
    """One user's unlock of one file by rewarded ads."""

    class Status(models.TextChoices):
        OPEN = "open", "Counting ads"
        READY = "ready", "Unlocked, waiting for the download"
        REDEEMED = "redeemed", "Used by a download"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    class Kind(models.TextChoices):
        FREE = "free", "Free file"
        FEATURED = "featured", "Featured file"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ad_unlock_tickets",
    )
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, related_name="+"
    )
    object_id = models.UUIDField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    required = models.PositiveSmallIntegerField(
        help_text="Ads to watch in this cycle (the package rule when it started)."
    )
    progress = models.PositiveSmallIntegerField(default=0)
    cycle = models.PositiveIntegerField(
        default=1, help_text="How many times this user started an unlock of this file."
    )
    package = models.ForeignKey(
        PackageTier,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The package whose rule started this cycle.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    last_progress_at = models.DateTimeField(null=True, blank=True)
    unlocked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Ad unlock ticket"
        verbose_name_plural = "Ad unlock tickets"
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "content_type", "object_id"],
                name="shop_one_unlock_ticket_per_user_file",
            ),
            models.CheckConstraint(
                condition=models.Q(required__gte=1),
                name="shop_unlock_ticket_requires_an_ad",
            ),
            models.CheckConstraint(
                condition=models.Q(progress__lte=models.F("required")),
                name="shop_unlock_ticket_progress_within_required",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "unlocked_at"], name="shop_unlock_user_idx"),
        ]

    def __str__(self) -> str:
        return f"Unlock {self.pk} ({self.status} {self.progress}/{self.required})"

    @property
    def reference(self) -> str:
        """The opaque reference the ads app stores on each watch of this ticket."""
        return f"unlock:{self.pk}"


class AdUnlockProgress(models.Model):
    """One rewarded watch counted by a ticket; ``view_id`` is unique, so exactly once."""

    ticket = models.ForeignKey(
        AdUnlockTicket, on_delete=models.CASCADE, related_name="steps"
    )
    view_id = models.PositiveBigIntegerField(
        unique=True, help_text="The rewarded ad view this step came from."
    )
    cycle = models.PositiveIntegerField(default=1)
    number = models.PositiveSmallIntegerField(
        help_text="Position in the cycle (1-based)."
    )
    counted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Ad unlock step"
        verbose_name_plural = "Ad unlock steps"
        ordering: ClassVar[list[str]] = ["ticket_id", "cycle", "number"]

    def __str__(self) -> str:
        return f"Ticket {self.ticket_id} step {self.number} (view {self.view_id})"
