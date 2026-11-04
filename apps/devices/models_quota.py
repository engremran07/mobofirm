from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserDeviceQuota(models.Model):
    WINDOW_CHOICES = [
        ("3m", "3 Months"),
        ("6m", "6 Months"),
        ("12m", "12 Months"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_quotas"
    )
    max_devices = models.PositiveIntegerField(
        null=True, blank=True, help_text="Override max devices; null = default"
    )
    window = models.CharField(max_length=4, choices=WINDOW_CHOICES, default="6m")
    last_reset_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "User Device Quota"
        verbose_name_plural = "User Device Quotas"
        indexes = [models.Index(fields=["user"])]

    def __str__(self) -> str:
        return f"Device quota for {self.user_id}"  # type: ignore[attr-defined]
