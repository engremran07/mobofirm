"""
Blog Post Versioning Models
============================

Content versioning for editorial workflow.
"""

from django.conf import settings
from django.db import models


class PostVersion(models.Model):
    """
    Historical versions of post content for audit trail and rollback.
    """

    post = models.ForeignKey(
        "blog.Post", on_delete=models.CASCADE, related_name="versions"
    )
    version_number = models.PositiveIntegerField()

    # Versioned fields (snapshot of post at this version)
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    body = models.TextField()

    # Version metadata
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="post_versions_created",
    )
    change_summary = models.TextField(
        blank=True, help_text="What changed in this version"
    )

    class Meta:
        ordering = ["-version_number"]
        unique_together = [["post", "version_number"]]
        indexes = [
            models.Index(fields=["post", "-version_number"]),
            models.Index(fields=["created_by", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.post.title} v{self.version_number}"


class PostRevisionRequest(models.Model):
    """
    Editorial workflow - request revisions before publishing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    post = models.ForeignKey(
        "blog.Post", on_delete=models.CASCADE, related_name="revision_requests"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="revision_requests_made",
    )
    requested_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="revision_requests_received",
    )
    notes = models.TextField(help_text="What needs to be revised")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["post", "status"]),
            models.Index(fields=["requested_from", "status"]),
        ]

    def __str__(self):
        return f"Revision request for {self.post.title} by {self.requested_by}"


__all__ = ["PostRevisionRequest", "PostVersion"]
