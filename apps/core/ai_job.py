from __future__ import annotations

from django.conf import settings
from django.db import models


class AIJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
    ]

    entity_type = models.CharField(
        max_length=100, help_text="e.g., seo.link, tag.suggestion"
    )
    entity_id = models.CharField(max_length=64, blank=True, null=True)  # noqa: DJ001
    task_type = models.CharField(
        max_length=100, help_text="e.g., embed, moderate, translate"
    )
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    model_used = models.CharField(max_length=100, blank=True, null=True)  # noqa: DJ001
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["entity_type", "task_type"], name="ai_job_entity_task_idx"
            ),
            models.Index(fields=["status"], name="ai_job_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.task_type} ({self.status})"
