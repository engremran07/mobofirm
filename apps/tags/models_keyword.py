from __future__ import annotations

from django.db import models


class KeywordProvider(models.Model):
    name = models.CharField(max_length=100, unique=True)
    source = models.CharField(max_length=50, default="custom")
    api_key = models.CharField(max_length=255, blank=True, default="")
    base_params = models.JSONField(default=dict, blank=True)
    is_enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=50, blank=True, default="")
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Keyword Provider"
        verbose_name_plural = "Keyword Providers"

    def __str__(self):
        return self.name


class KeywordSuggestion(models.Model):
    provider = models.ForeignKey(
        KeywordProvider, on_delete=models.CASCADE, related_name="suggestions"
    )
    keyword = models.CharField(max_length=128)
    normalized = models.CharField(max_length=128, db_index=True)
    score = models.FloatField(default=0.0)
    locale = models.CharField(max_length=20, blank=True, default="")
    category = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("provider", "normalized")
        indexes = [
            models.Index(fields=["normalized"]),
            models.Index(fields=["locale", "category"]),
        ]

    def __str__(self):
        return f"{self.keyword} ({self.provider})"
