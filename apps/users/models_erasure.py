"""Audit trail for account erasure (anonymisation in place).

One row per erasure request. It is the tombstone of the process: who asked, who
confirmed, when the grace period ends, when it ran, what every app reported and which
apps had no handler. It never holds personal data about the erased person (only the
user's primary key, which stays in place because ledgers reference it), so it can be
kept for as long as accountability requires.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q


class AccountErasure(models.Model):
    """One request to anonymise an account, from request to terminal outcome."""

    class Status(models.TextChoices):
        """Lifecycle of a request."""

        AWAITING_CONFIRMATION = "awaiting_confirmation", "Awaiting email confirmation"
        SCHEDULED = "scheduled", "Scheduled (grace period)"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Completed, apps without a handler remain"
        FAILED = "failed", "Failed (needs staff)"

    class Source(models.TextChoices):
        """Who started the request."""

        SELF_SERVICE = "self_service", "Requested by the account holder"
        STAFF = "staff", "Performed by staff"

    #: States in which a request is still open (at most one per account).
    OPEN_STATUSES: ClassVar[tuple[str, ...]] = (
        Status.AWAITING_CONFIRMATION,
        Status.SCHEDULED,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="erasure_requests",
        help_text="The account; cleared only if the row is hard-deleted afterwards.",
    )
    user_pk = models.PositiveBigIntegerField(
        db_index=True, help_text="Primary key of the account (stays in ledgers)."
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.AWAITING_CONFIRMATION,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reason = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="Why staff erased the account. Do not put personal data here.",
    )
    token_hash = models.CharField(max_length=64, blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    failure_reason = models.CharField(max_length=160, blank=True, default="")
    receipt = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-app counts reported by each anonymiser, and apps without one.",
    )

    class Meta:
        verbose_name = "Account erasure"
        verbose_name_plural = "Account erasures"
        ordering: ClassVar[list[str]] = ["-requested_at"]
        constraints = (
            models.UniqueConstraint(
                fields=["user_pk"],
                condition=Q(status__in=["awaiting_confirmation", "scheduled"]),
                name="users_erasure_one_open_per_account",
            ),
        )
        indexes = (
            models.Index(
                fields=["status", "scheduled_for"], name="users_erasure_due_idx"
            ),
        )

    def __str__(self) -> str:
        return f"Erasure #{self.pk} of account {self.user_pk} ({self.status})"

    @property
    def is_open(self) -> bool:
        """Whether the request can still be confirmed, cancelled or executed."""
        return self.status in self.OPEN_STATUSES
