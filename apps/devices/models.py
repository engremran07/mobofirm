from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from solo.models import SingletonModel


class Device(models.Model):
    """
    Primary device identity record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
        db_index=True,  # CRITICAL: Heavily queried in my_devices view
    )
    # OS fingerprint (primary identity, per user per OS)
    os_fingerprint = models.CharField(
        max_length=128, db_index=True, blank=True, default=""
    )
    os_name = models.CharField(max_length=50, blank=True, default="")
    os_version = models.CharField(max_length=50, blank=True, default="")
    # Legacy fields kept for compatibility; not used for identity
    hardware_uuid = models.CharField(
        max_length=128, db_index=True, blank=True, default=""
    )
    device_key_id = models.CharField(
        max_length=128, db_index=True, blank=True, default=""
    )
    key_algorithm = models.CharField(
        max_length=20,
        choices=[("ES256", "ES256"), ("Ed25519", "Ed25519")],
        default="ES256",
        blank=True,
    )
    public_key = models.TextField(blank=True, default="")
    fingerprint_hash = models.CharField(max_length=128, blank=True, default="")
    display_name = models.CharField(max_length=150, blank=True, default="")
    browser_family = models.CharField(max_length=50, blank=True, default="")
    os_family = models.CharField(max_length=50, blank=True, default="")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    is_trusted = models.BooleanField(default=True)
    is_blocked = models.BooleanField(default=False)
    risk_score = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    metadata = models.JSONField(default=dict, blank=True)
    max_privilege_level = models.CharField(
        max_length=20,
        choices=[
            ("normal", "Normal"),
            ("restricted", "Restricted"),
            ("blocked", "Blocked"),
        ],
        default="normal",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "os_fingerprint"],
                name="device_user_os_fingerprint_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "os_fingerprint"], name="device_user_osfp_idx"
            ),
            models.Index(fields=["is_blocked"], name="device_blocked_idx"),
            models.Index(fields=["last_seen_at"], name="device_last_seen_idx"),
            models.Index(fields=["risk_score"], name="device_risk_idx"),
        ]
        ordering = ["-last_seen_at"]

    def __str__(self) -> str:
        label = self.display_name or self.os_fingerprint or self.hardware_uuid
        return f"{getattr(self.user, 'email', self.user_id)} :: {label}"  # type: ignore[attr-defined]

    @property
    def machine_uuid(self) -> str:
        """
        Legacy alias for compatibility with older code paths.
        """
        return self.hardware_uuid or self.os_fingerprint


class DeviceConfig(SingletonModel):
    """
    Global defaults and capabilities for device identity.
    """

    basic_fingerprinting_enabled = models.BooleanField(default=True)
    enhanced_fingerprinting_enabled = models.BooleanField(default=False)
    enterprise_device_management_enabled = models.BooleanField(default=False)
    max_devices_default = models.PositiveIntegerField(default=5)
    monthly_device_quota = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum new devices per user per month (None = unlimited).",
    )
    yearly_device_quota = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum new devices per user per calendar year (None = unlimited).",
    )
    ad_unlock_enabled = models.BooleanField(
        default=False,
        help_text="Allow users to unlock device quota by completing rewarded ads.",
    )
    strict_new_device_login = models.BooleanField(default=False)
    require_mfa_on_new_device = models.BooleanField(default=False)
    device_expiry_days = models.PositiveIntegerField(null=True, blank=True)
    allow_server_fallback = models.BooleanField(default=True)
    ai_risk_scoring_enabled = models.BooleanField(default=False)
    risk_mfa_threshold = models.PositiveIntegerField(
        default=75,
        help_text="If a device risk score meets/exceeds this value, require MFA to continue.",
    )

    class Meta:
        verbose_name = "Device Config"

    def __str__(self) -> str:
        return "Device Config"


class AppPolicy(models.Model):
    """
    Per-app/device policy that can be referenced by settings.DEVICE_APP_NAME.
    """

    name = models.CharField(max_length=100, unique=True)
    basic_fingerprinting = models.BooleanField(default=True)
    enhanced_fingerprinting = models.BooleanField(default=False)
    device_locking_mode = models.CharField(
        max_length=20,
        choices=[("none", "None"), ("soft", "Soft"), ("strict", "Strict")],
        default="none",
    )
    mfa_requirement = models.CharField(
        max_length=20,
        choices=[("none", "None"), ("new_device", "New Device"), ("always", "Always")],
        default="none",
    )
    ai_risk_scoring = models.BooleanField(default=False)
    service_level_rules = models.JSONField(default=dict, blank=True)
    monthly_device_quota = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum new devices per user per month for this app (None = use global).",
    )
    yearly_device_quota = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum new devices per user per calendar year for this app (None = use global).",
    )
    ad_unlock_enabled = models.BooleanField(
        default=False,
        help_text="Allow ad-based unlocks for device quota in this app.",
    )

    class Meta:
        verbose_name = "App Policy"
        verbose_name_plural = "App Policies"

    def __str__(self) -> str:
        return self.name


class DeviceEvent(models.Model):
    """
    Device activity/event log (AI-ready).
    """

    EVENT_CHOICES = [
        ("login", "Login"),
        ("logout", "Logout"),
        ("download_attempt", "Download Attempt"),
        ("policy_violation", "Policy Violation"),
        ("registration", "Registration"),
        ("device_trusted", "Device Trusted"),
        ("device_blocked", "Device Blocked"),
        ("mfa_challenge", "MFA Challenge"),
        ("mfa_failed", "MFA Failed"),
        ("mfa_passed", "MFA Passed"),
        ("service_access", "Service Access"),
    ]

    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="device_events",
    )
    event_type = models.CharField(max_length=50, choices=EVENT_CHOICES)
    ip = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=512, blank=True, default="")
    geo_region = models.CharField(max_length=100, blank=True, default="")
    success = models.BooleanField(default=True)
    reason = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"], name="device_event_user_idx"),
            models.Index(fields=["device"], name="device_event_device_idx"),
            models.Index(fields=["event_type"], name="device_event_type_idx"),
            models.Index(fields=["created_at"], name="device_event_created_idx"),
            models.Index(fields=["success"], name="device_event_success_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.created_at}"


# =============================================================================
# Device registry models
# =============================================================================


class DeviceFingerprint(models.Model):
    """Anonymous device fingerprint for quota/trust tracking."""

    class TrustLevel(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        TRUSTED = "trusted", "Trusted"

    fingerprint_hash = models.CharField(max_length=128, unique=True, db_index=True)
    signals = models.JSONField(default=dict, blank=True)
    browser = models.CharField(max_length=100, blank=True, default="")
    os = models.CharField(max_length=100, blank=True, default="")
    device_type = models.CharField(max_length=50, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    country = models.CharField(max_length=3, blank=True, default="")
    trust_level = models.CharField(
        max_length=12, choices=TrustLevel.choices, default=TrustLevel.UNKNOWN
    )
    is_bot = models.BooleanField(default=False)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    visit_count = models.PositiveIntegerField(default=1)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="device_fingerprints",
    )

    class Meta:
        db_table = "device_registry_devicefingerprint"
        verbose_name = "Device Fingerprint"
        verbose_name_plural = "Device Fingerprints"
        ordering = ["-last_seen"]

    def __str__(self) -> str:
        return f"{self.fingerprint_hash[:16]}… [{self.trust_level}]"


class TrustScore(models.Model):
    """Computed trust score for a device fingerprint."""

    fingerprint = models.OneToOneField(
        DeviceFingerprint, on_delete=models.CASCADE, related_name="trust_score"
    )
    score = models.FloatField(default=0.0)
    signals_passed = models.PositiveIntegerField(default=0)
    signals_failed = models.PositiveIntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "device_registry_trustscore"
        verbose_name = "Trust Score"

    def __str__(self) -> str:
        return f"{self.fingerprint.fingerprint_hash[:16]}… score={self.score}"


class QuotaTier(models.Model):
    """Download quota tier assigned by trust level."""

    name = models.CharField(max_length=64, unique=True)
    min_trust_score = models.FloatField(default=0.0)
    max_trust_score = models.FloatField(default=100.0)
    daily_download_limit = models.PositiveIntegerField(default=5)
    hourly_download_limit = models.PositiveIntegerField(default=2)
    requires_ad = models.BooleanField(default=False)
    can_bypass_captcha = models.BooleanField(default=False)

    class Meta:
        db_table = "device_registry_quotatier"
        verbose_name = "Quota Tier"
        verbose_name_plural = "Quota Tiers"
        ordering = ["min_trust_score"]

    def __str__(self) -> str:
        return self.name


class RegistryEvent(models.Model):
    """Fingerprint-level event log (visit, download, etc.)."""

    class Action(models.TextChoices):
        VISIT = "visit", "Visit"
        DOWNLOAD_START = "dl_start", "Download Start"
        DOWNLOAD_COMPLETE = "dl_complete", "Download Complete"
        AD_VIEW = "ad_view", "Ad View"
        LOGIN = "login", "Login"
        SIGNUP = "signup", "Signup"
        FP_CHANGE = "fp_change", "Fingerprint Change"

    fingerprint = models.ForeignKey(
        DeviceFingerprint, on_delete=models.CASCADE, related_name="registry_events"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="registry_events",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "device_registry_deviceevent"
        verbose_name = "Registry Event"
        verbose_name_plural = "Registry Events"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} @ {self.created_at}"


class BehaviorInsight(models.Model):
    """AI-flagged behavioral anomaly for a fingerprint."""

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RESOLVED = "resolved", "Resolved"

    fingerprint = models.ForeignKey(
        DeviceFingerprint,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="behavior_insights",
    )
    related_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="behavior_insights",
    )
    severity = models.CharField(
        max_length=10, choices=Severity.choices, default=Severity.LOW
    )
    recommendation = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "device_registry_behaviorinsight"
        verbose_name = "Behavior Insight"
        verbose_name_plural = "Behavior Insights"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.severity} insight [{self.status}]"


# (GSMArena Sync models live in apps.firmwares — see apps/firmwares/models.py)
