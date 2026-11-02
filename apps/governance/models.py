import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ReviewCase(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        IN_REVIEW = "in_review", "In review"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"
        CORRECTION_REQUIRED = "correction_required", "Correction required"

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_submissions",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_reviews",
    )
    publisher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_publications",
    )
    result_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="governance_results",
    )
    result_object_id = models.CharField(max_length=64, blank=True, default="")
    result_object = GenericForeignKey("result_content_type", "result_object_id")
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.SUBMITTED, db_index=True
    )
    revision = models.PositiveIntegerField(default=1)
    decision_data = models.JSONField(default=dict, blank=True)
    is_open = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        permissions = [
            ("can_review_content", "Can review governed content"),
            ("can_publish_content", "Can publish approved content"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=models.Q(is_open=True),
                name="governance_one_open_case",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="governance_status_created_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.content_type} #{self.object_id} ({self.status})"

    def delete(self, *args, **kwargs):
        raise ValidationError("Governance cases are retained as an audit record.")


class ReviewEvent(models.Model):
    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        RESUBMITTED = "resubmitted", "Resubmitted"
        REVIEW_STARTED = "review_started", "Review started"
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"
        FULFILLED = "fulfilled", "Fulfilled"
        CORRECTION_REQUIRED = "correction_required", "Correction required"

    case = models.ForeignKey(
        ReviewCase, on_delete=models.PROTECT, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_events",
    )
    action = models.CharField(max_length=24, choices=Action.choices)
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24)
    rationale = models.CharField(max_length=1000, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["case_id", "occurred_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["case", "idempotency_key"],
                name="governance_event_idempotency",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Governance events are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Governance events are append-only.")
