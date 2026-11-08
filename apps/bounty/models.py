from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Bounty(models.Model):
    """Credits offered for fulfilling a firmware request, model or variant."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FULFILLED = "fulfilled", "Fulfilled"
        REVIEW = "review", "Needs staff review"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    UNSETTLED: ClassVar[tuple[str, ...]] = (Status.OPEN, Status.REVIEW)

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bounties"
    )
    title = models.CharField(max_length=200)
    note = models.TextField(blank=True, max_length=1000)
    target_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    target_id = models.PositiveBigIntegerField()
    target_label = models.CharField(max_length=200)
    credits = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    review_reason = models.CharField(max_length=255, blank=True)
    escrow_hold_id = models.PositiveBigIntegerField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bounties_won",
    )
    settled_firmware = models.CharField(max_length=100, blank=True)
    closed_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        verbose_name = _("bounty")
        verbose_name_plural = _("bounties")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(credits__gt=0), name="bounty_credits_positive"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["status", "expires_at"], name="bounty_due_idx"),
            models.Index(
                fields=["target_type", "target_id", "status"], name="bounty_target_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"Bounty #{self.pk}: {self.credits} credits for {self.target_label}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def escrow_key(self) -> str:
        return f"bounty:{self.pk}:escrow"


class BountyClaim(models.Model):
    """A visible, non-binding "I'm working on this" on a bounty."""

    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name="claims")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bounty_claims"
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["bounty", "user"], name="bounty_claim_once_per_user"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} on bounty #{self.bounty_id}"


class BountyDispute(models.Model):
    """A problem reported on an unsettled bounty; resolved by staff."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    class Outcome(models.TextChoices):
        REFUNDED = "refunded", "Refunded to the poster"
        PAID = "paid", "Paid to the payee"
        DISMISSED = "dismissed", "Dismissed"

    bounty = models.ForeignKey(
        Bounty, on_delete=models.CASCADE, related_name="disputes"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bounty_disputes",
    )
    reason = models.TextField(max_length=1000)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    payee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text=_("Who is paid if staff pay out this bounty."),
    )
    outcome = models.CharField(max_length=10, choices=Outcome.choices, blank=True)
    resolution_note = models.CharField(max_length=500, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["status", "-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["bounty", "reporter"],
                condition=models.Q(status="open"),
                name="bounty_one_open_dispute_per_reporter",
            ),
        ]

    def __str__(self) -> str:
        return f"Dispute on bounty #{self.bounty_id} ({self.status})"


class BountyLog(models.Model):
    """Append-only audit trail of everything that happens to a bounty."""

    bounty = models.ForeignKey(Bounty, on_delete=models.CASCADE, related_name="log")
    kind = models.CharField(max_length=24, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    detail = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.kind} on bounty #{self.bounty_id}"
