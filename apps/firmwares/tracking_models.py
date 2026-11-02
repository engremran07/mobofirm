# Firmware Tracking and Analytics Models
"""
COMPREHENSIVE FIRMWARE ANALYTICS SYSTEM
========================================

Models for tracking user interactions, trending analysis, and request management:

1. FirmwareView - Individual page views with session tracking (for trending calculations)
2. FirmwareDownloadAttempt - Download tracking including failures (critical for SA quota accuracy)
3. FirmwareRequest - User requests for missing firmwares (prioritization insights)
4. FirmwareStats - Aggregated daily statistics (optimized for dashboard queries)

Key Features:
- Generic FK support for all firmware types (Official, Engineering, etc.)
- Atomic operations to prevent race conditions
- Optimized indexes for high-write volume
- Daily aggregation via Celery (see tasks.py)
- Powers homepage trending widgets

Performance Notes:
- Views/downloads logged async via Celery tasks (non-blocking)
- Stats aggregated daily to reduce query load
- 90-day data retention (configurable)
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class FirmwareView(models.Model):  # noqa: DJ008
    """Track individual firmware views for analytics - optimized for high write volume"""

    # GenericForeignKey to support all firmware types (Official, Engineering, etc.)
    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.contrib.contenttypes.models import ContentType

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()  # Firmware ID
    firmware = GenericForeignKey("content_type", "object_id")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who viewed (null for anonymous)",
    )
    session_key = models.CharField(
        max_length=40, db_index=True, help_text="For anonymous tracking"
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    referer = models.URLField(max_length=500, blank=True)
    viewed_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "firmwares_view_log"
        indexes = [
            models.Index(
                fields=["content_type", "object_id", "-viewed_at"]
            ),  # Quick lookups per firmware
            models.Index(fields=["-viewed_at"]),  # Trending queries
        ]
        ordering = ["-viewed_at"]

    @classmethod
    def log_view(cls, firmware, user=None, session_key=None, request=None):
        """Convenient method to log a view with optional request context"""
        data = {
            "content_type": ContentType.objects.get_for_model(firmware),  # noqa: F821  # type: ignore[name-defined]
            "object_id": firmware.id,
            "user": user,
            "session_key": session_key,
        }

        if request:
            data.update(
                {
                    "ip_address": request.META.get("REMOTE_ADDR"),
                    "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
                    "referer": request.META.get("HTTP_REFERER", "")[:500],
                }
            )

        return cls.objects.create(**data)


class FirmwareDownloadAttempt(models.Model):  # noqa: DJ008
    """Track download attempts including failures - critical for storage analytics"""

    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.contrib.contenttypes.models import ContentType

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    firmware = GenericForeignKey("content_type", "object_id")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        help_text="User who attempted download",
    )

    # Storage integration (if using storage app)
    storage_session_id = models.UUIDField(
        null=True, blank=True, help_text="UserDownloadSession ID if applicable"
    )

    # Status tracking
    STATUS_CHOICES = [
        ("initiated", "Initiated"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("downloading", "Downloading"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="initiated", db_index=True
    )

    initiated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    failure_reason = models.CharField(max_length=500, blank=True)
    bytes_transferred = models.BigIntegerField(
        default=0, help_text="Actual bytes downloaded"
    )

    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "firmwares_download_attempt"
        indexes = [
            models.Index(fields=["content_type", "object_id", "-initiated_at"]),
            models.Index(fields=["status", "-initiated_at"]),
            models.Index(fields=["-initiated_at"]),
        ]
        ordering = ["-initiated_at"]

    def mark_completed(self, bytes_transferred=0):
        """Mark download as successfully completed"""
        self.status = "completed"
        self.completed_at = timezone.now()
        self.bytes_transferred = bytes_transferred
        self.save(update_fields=["status", "completed_at", "bytes_transferred"])

    def mark_failed(self, reason):
        """Mark download as failed with reason"""
        self.status = "failed"
        self.failed_at = timezone.now()
        self.failure_reason = reason[:500]
        self.save(update_fields=["status", "failed_at", "failure_reason"])


class FirmwareRequest(models.Model):
    """User requests for firmwares that don't exist yet - valuable for prioritization"""

    brand = models.ForeignKey(
        "firmwares.Brand", on_delete=models.CASCADE, related_name="requests"
    )
    model = models.ForeignKey(
        "firmwares.Model",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="requests",
    )
    variant_name = models.CharField(
        max_length=200, blank=True, help_text="Requested variant (may not exist yet)"
    )

    firmware_type = models.CharField(
        max_length=50,
        choices=[
            ("official", "Official"),
            ("engineering", "Engineering"),
            ("readback", "Readback"),
            ("modified", "Modified"),
            ("other", "Other"),
        ],
        default="official",
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="firmware_requests",
    )

    description = models.TextField(blank=True, help_text="Additional details from user")
    urgency = models.IntegerField(
        default=1,
        choices=[(1, "Low"), (2, "Normal"), (3, "High"), (4, "Urgent")],
        help_text="User-indicated urgency",
    )

    # Tracking
    request_count = models.IntegerField(
        default=1, help_text="Number of users who requested this"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_requested_at = models.DateTimeField(auto_now=True)

    # Status
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("fulfilled", "Fulfilled"),
        ("rejected", "Rejected"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="open", db_index=True
    )
    fulfilled_firmware_ct = models.ForeignKey(
        "contenttypes.ContentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Content type of firmware that fulfilled this",
    )
    fulfilled_firmware_id = models.UUIDField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)

    class Meta:
        db_table = "firmwares_request"
        indexes = [
            models.Index(
                fields=["status", "-request_count"]
            ),  # Most requested open items
            models.Index(fields=["brand", "model", "status"]),
            models.Index(fields=["-created_at"]),
        ]
        unique_together = [
            ("brand", "model", "variant_name", "firmware_type")
        ]  # Prevent duplicates
        ordering = ["-request_count", "-last_requested_at"]

    def __str__(self):
        model_str = f"/{self.model.name}" if self.model else ""
        variant_str = f"/{self.variant_name}" if self.variant_name else ""
        return f"{self.brand.name}{model_str}{variant_str} ({self.firmware_type}) - {self.request_count} requests"

    def increment_request(self, user=None):
        """Increment request count when another user requests same firmware"""
        from django.db.models import F

        self.__class__.objects.filter(id=self.id).update(  # type: ignore[attr-defined]
            request_count=F("request_count") + 1, last_requested_at=timezone.now()
        )
        self.refresh_from_db()


class FirmwareStats(models.Model):  # noqa: DJ008
    """Aggregated daily statistics per firmware - optimized for dashboard queries"""

    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.contrib.contenttypes.models import ContentType

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    firmware = GenericForeignKey("content_type", "object_id")

    date = models.DateField(db_index=True, help_text="Stats for this date")

    # Daily metrics
    view_count = models.IntegerField(default=0)
    unique_visitors = models.IntegerField(default=0)
    download_attempts = models.IntegerField(default=0)
    successful_downloads = models.IntegerField(default=0)
    failed_downloads = models.IntegerField(default=0)
    bytes_transferred = models.BigIntegerField(default=0)

    # Calculated metrics
    conversion_rate = models.FloatField(
        default=0.0, help_text="Downloads / Views percentage"
    )
    success_rate = models.FloatField(
        default=0.0, help_text="Successful / Total attempts percentage"
    )

    class Meta:
        db_table = "firmwares_stats"
        unique_together = [("content_type", "object_id", "date")]
        indexes = [
            models.Index(fields=["date", "-view_count"]),  # Trending by date
            models.Index(fields=["date", "-download_attempts"]),
            models.Index(fields=["content_type", "object_id", "date"]),
        ]
        ordering = ["-date"]

    def calculate_rates(self):
        """Recalculate conversion and success rates"""
        self.conversion_rate = (
            (self.successful_downloads / self.view_count * 100)
            if self.view_count > 0
            else 0.0
        )
        self.success_rate = (
            (self.successful_downloads / self.download_attempts * 100)
            if self.download_attempts > 0
            else 0.0
        )
        self.save(update_fields=["conversion_rate", "success_rate"])

    @classmethod
    def aggregate_for_date(cls, date, firmware_ct, firmware_id):
        """Aggregate raw logs into daily stats (run by Celery task)"""
        from django.db.models import Sum

        # Get or create stats record
        stats, _created = cls.objects.get_or_create(
            content_type=firmware_ct, object_id=firmware_id, date=date
        )

        # Count views
        views = FirmwareView.objects.filter(
            content_type=firmware_ct, object_id=firmware_id, viewed_at__date=date
        )
        stats.view_count = views.count()
        stats.unique_visitors = (
            views.values("user").distinct().count()
            + views.filter(user__isnull=True).values("session_key").distinct().count()
        )

        # Count downloads
        downloads = FirmwareDownloadAttempt.objects.filter(
            content_type=firmware_ct, object_id=firmware_id, initiated_at__date=date
        )
        stats.download_attempts = downloads.count()
        stats.successful_downloads = downloads.filter(status="completed").count()
        stats.failed_downloads = downloads.filter(status="failed").count()
        stats.bytes_transferred = (
            downloads.aggregate(total=Sum("bytes_transferred"))["total"] or 0
        )

        stats.calculate_rates()
        return stats
