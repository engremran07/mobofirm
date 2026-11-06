from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.utils import timezone
from solo.models import SingletonModel

from apps.core.models import SoftDeleteModel, TimestampedModel

COMMENT_MIN_LENGTH = 3
COMMENT_MAX_LENGTH = 5000


class Comment(TimestampedModel, SoftDeleteModel):
    """A user comment on a blog post, optionally threaded under a parent comment.

    New comments are scored by ``apps.comments.services.moderation``: clean comments
    are approved immediately, anything suspicious waits in ``PENDING`` for staff.
    ``moderation_flags`` keeps the automatic assessment and, under ``decision``,
    the latest staff decision (who, when, why).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SPAM = "spam", "Spam"

    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    content_object = GenericForeignKey("content_type", "object_id")
    post = models.ForeignKey(
        "blog.Post",
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField(
        validators=[
            MinLengthValidator(COMMENT_MIN_LENGTH),
            MaxLengthValidator(COMMENT_MAX_LENGTH),
        ],
        help_text="Plain text; rendered escaped. 3-5000 characters.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    is_approved = models.BooleanField(
        default=False,
        help_text="Kept in step with status=approved.",
    )
    score = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    moderation_flags = models.JSONField(default=dict, blank=True)
    toxicity_score = models.FloatField(default=0.0)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        verbose_name = "Comment"
        verbose_name_plural = "Comments"
        indexes = (
            models.Index(fields=["content_type", "object_id", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["post", "status"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["toxicity_score"]),
        )

    def __str__(self) -> str:
        target = self.post or self.content_object or "unknown"
        return f"Comment by {self.user} on {target}"

    def save(self, *args, **kwargs):
        """Keep the generic target in step with ``post`` and ``is_approved`` with status."""
        if self.post and not (self.content_type and self.object_id):
            self.content_type = ContentType.objects.get_for_model(self.post)
            self.object_id = self.post.pk
        self.is_approved = self.status == self.Status.APPROVED
        super().save(*args, **kwargs)


class CommentVote(models.Model):
    """One upvote per user and comment."""

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comment_votes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["comment", "user"], name="comments_vote_unique_user_comment"
            ),
        )

    def __str__(self) -> str:
        return f"Vote by {self.user_id} on comment {self.comment_id}"


class CommentReport(models.Model):
    """A reader's report; enough distinct reports re-queue the comment for review."""

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam or advertising"
        ABUSE = "abuse", "Abusive or hateful"
        OFF_TOPIC = "off_topic", "Off topic"
        OTHER = "other", "Something else"

    comment = models.ForeignKey(
        Comment, on_delete=models.CASCADE, related_name="reports"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comment_reports",
    )
    reason = models.CharField(
        max_length=20, choices=Reason.choices, default=Reason.OTHER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["comment", "user"], name="comments_report_unique_user_comment"
            ),
        )

    def __str__(self) -> str:
        return f"Report by {self.user_id} on comment {self.comment_id}"


class CommentSettings(SingletonModel):
    """Site-wide comment switches and automatic-moderation thresholds."""

    enable_comments = models.BooleanField(default=True)
    auto_approve_clean = models.BooleanField(
        default=True,
        help_text="Publish comments that pass every automatic check immediately.",
    )
    max_links = models.PositiveSmallIntegerField(
        default=2,
        help_text="Links allowed in one comment before it is queued for review.",
    )
    report_threshold = models.PositiveSmallIntegerField(
        default=3,
        help_text="Distinct reader reports that send an approved comment back to review.",
    )

    class Meta:
        verbose_name = "Comment Settings"

    def __str__(self) -> str:
        return "Comment Settings"
