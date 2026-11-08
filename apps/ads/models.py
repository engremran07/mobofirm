from __future__ import annotations

import logging
from typing import ClassVar

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from solo.models import SingletonModel

from apps.core.models import AuditFieldsModel, SoftDeleteModel, TimestampedModel

from .zones import DEVICE_ALL, DEVICE_CHOICES, ZONE_CHOICES

logger = logging.getLogger(__name__)

# Failures of apps.firmwares.crypto: missing/invalid FERNET_KEY, tampered or foreign
# ciphertext, and ciphertext that does not decode to text.
_ENCRYPT_ERRORS = (ImproperlyConfigured,)
_DECRYPT_ERRORS = (ImproperlyConfigured, InvalidToken, ValueError)

# ==================== AD NETWORK PROVIDERS ====================


class AdNetwork(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents an ad network provider configuration.

    Supports multiple ad networks beyond just AdSense:
    - Google AdSense, Ad Manager
    - Media.net (Yahoo/Bing)
    - Ezoic, AdThrive, Mediavine (premium)
    - PropellerAds, Infolinks, Carbon
    - Amazon Publisher Services
    - Custom/Direct ad servers

    Each network has its own configuration, credentials, and ad unit definitions.
    """

    NETWORK_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("adsense", "Google AdSense"),
        ("admanager", "Google Ad Manager (DFP)"),
        ("medianet", "Media.net"),
        ("ezoic", "Ezoic"),
        ("adthrive", "AdThrive"),
        ("mediavine", "Mediavine"),
        ("propellerads", "PropellerAds"),
        ("infolinks", "Infolinks"),
        ("carbon", "Carbon Ads"),
        ("buysellads", "BuySellAds"),
        ("amazon", "Amazon Publisher Services"),
        ("taboola", "Taboola"),
        ("outbrain", "Outbrain"),
        ("revcontent", "RevContent"),
        ("mgid", "MGID"),
        ("adsterra", "Adsterra"),
        ("custom", "Custom Ad Server"),
        ("direct", "Direct Sales"),
    ]

    name = models.CharField(max_length=100, unique=True)
    network_type = models.CharField(max_length=30, choices=NETWORK_TYPES)
    is_enabled = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(
        default=10, help_text="Higher priority networks are tried first (waterfall)."
    )

    # Authentication
    publisher_id = models.CharField(max_length=100, blank=True, default="")
    # FINDING-0010: Store API credentials as Fernet-encrypted ciphertext.
    # Never access these fields directly — use get_api_key() / set_api_key().
    api_key = models.TextField(
        blank=True, default="", help_text="Fernet-encrypted API key."
    )
    api_secret = models.TextField(
        blank=True, default="", help_text="Fernet-encrypted API secret."
    )

    def set_api_key(self, plaintext: str) -> None:
        """Encrypt and store the API key."""
        if not plaintext:
            self.api_key = ""
            return
        try:
            from apps.firmwares.crypto import encrypt_password

            self.api_key = encrypt_password(plaintext)
        except _ENCRYPT_ERRORS:
            logger.warning(
                "AdNetwork.set_api_key: encryption unavailable, storing blank"
            )
            self.api_key = ""

    def get_api_key(self) -> str:
        """Decrypt and return the API key, or empty string on failure."""
        if not self.api_key:
            return ""
        try:
            from apps.firmwares.crypto import decrypt_password

            return decrypt_password(self.api_key)
        except _DECRYPT_ERRORS:
            logger.warning("AdNetwork.get_api_key: decryption failed (id=%s)", self.pk)
            return ""

    def set_api_secret(self, plaintext: str) -> None:
        """Encrypt and store the API secret."""
        if not plaintext:
            self.api_secret = ""
            return
        try:
            from apps.firmwares.crypto import encrypt_password

            self.api_secret = encrypt_password(plaintext)
        except _ENCRYPT_ERRORS:
            logger.warning(
                "AdNetwork.set_api_secret: encryption unavailable, storing blank"
            )
            self.api_secret = ""

    def get_api_secret(self) -> str:
        """Decrypt and return the API secret, or empty string on failure."""
        if not self.api_secret:
            return ""
        try:
            from apps.firmwares.crypto import decrypt_password

            return decrypt_password(self.api_secret)
        except _DECRYPT_ERRORS:
            logger.warning(
                "AdNetwork.get_api_secret: decryption failed (id=%s)", self.pk
            )
            return ""

    # Script/Tag Configuration
    header_script = models.TextField(
        blank=True,
        default="",
        help_text="JavaScript to inject in <head> for this network.",
    )
    body_script = models.TextField(
        blank=True, default="", help_text="JavaScript to inject at end of <body>."
    )

    # Revenue Share
    revenue_share_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100.00,
        help_text="Percentage of revenue we keep (after network cut).",
    )

    # Supported Ad Types
    supports_display = models.BooleanField(default=True)
    supports_native = models.BooleanField(default=False)
    supports_video = models.BooleanField(default=False)
    supports_rewarded = models.BooleanField(default=False)
    supports_auto_ads = models.BooleanField(default=False)

    # Configuration
    config = models.JSONField(
        default=dict, blank=True, help_text="Network-specific configuration JSON."
    )

    # Status
    last_sync_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-priority", "name"]
        verbose_name = "Ad Network"
        verbose_name_plural = "Ad Networks"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_network_type_display()})"


class AdUnit(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents a specific ad unit within an ad network.

    Each network can have multiple ad units (slots, zones) with different
    sizes, formats, and targeting. This allows fine-grained control over
    which network ad units appear in which placements.
    """

    AD_FORMATS: ClassVar[list[tuple[str, str]]] = [
        ("display", "Display Banner"),
        ("native", "Native Ad"),
        ("video", "Video Ad"),
        ("rewarded", "Rewarded Video"),
        ("interstitial", "Interstitial"),
        ("anchor", "Anchor/Sticky"),
        ("in_article", "In-Article"),
        ("in_feed", "In-Feed"),
        ("multiplex", "Multiplex/Grid"),
    ]

    network = models.ForeignKey(
        AdNetwork, on_delete=models.CASCADE, related_name="ad_units"
    )
    name = models.CharField(max_length=100)
    unit_id = models.CharField(
        max_length=100,
        help_text="Ad unit ID from the network (slot ID, zone ID, etc.).",
    )
    ad_format = models.CharField(max_length=20, choices=AD_FORMATS, default="display")

    # Dimensions
    width = models.PositiveIntegerField(default=0, help_text="0 = responsive")
    height = models.PositiveIntegerField(default=0, help_text="0 = responsive")
    size_label = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="e.g., '300x250', 'responsive', 'fluid'",
    )

    # Status
    is_enabled = models.BooleanField(default=True)

    # Performance
    estimated_cpm = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=0,
        help_text="Estimated CPM for optimization.",
    )
    fill_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Historical fill rate percentage.",
    )

    # Render code
    render_code = models.TextField(
        blank=True, default="", help_text="Custom HTML/JS to render this ad unit."
    )

    class Meta:
        unique_together = ("network", "unit_id")
        ordering: ClassVar[list[str]] = ["network", "name"]

    def __str__(self) -> str:
        return f"{self.network.name} - {self.name} ({self.size_label or 'responsive'})"


# ==================== REWARDED ADS ====================


class RewardedAdConfig(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Configuration for one rewarded ad: what a user is given for watching it.

    The watch is proven by a server-controlled timer (no third-party SDK); every
    reward is granted through the owning app's contract: credits through the
    wallet's reward rule, slot resets through ``apps.devices.slots``, extra downloads
    and ad-free time through entitlements. Disabled by default.
    """

    class RewardType(models.TextChoices):
        CREDITS = "credits", "Credits (wallet reward rule)"
        DEVICE_RESET = "device_reset", "Device slot reset"
        DOWNLOAD_BONUS = "download_bonus", "Extra downloads"
        AD_FREE = "ad_free", "Ad-free period"

    name = models.CharField(max_length=100, unique=True)
    placement = models.ForeignKey(
        "AdPlacement",
        on_delete=models.CASCADE,
        related_name="rewarded_configs",
        null=True,
        blank=True,
        help_text="Required to enable: the placement whose creative is shown while the timer runs.",
    )
    network = models.ForeignKey(
        AdNetwork,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"supports_rewarded": True},
    )
    ad_unit = models.ForeignKey(
        AdUnit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"ad_format": "rewarded"},
    )

    is_enabled = models.BooleanField(default=False)

    # Reward Configuration
    reward_type = models.CharField(
        max_length=20, choices=RewardType.choices, default=RewardType.CREDITS
    )
    reward_amount = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Device reset: resets granted (1-3). Extra downloads: files added to the "
            "allowance. Ad-free: hours. Credits: ignored; the amount is the wallet's "
            "reward rule 'Watch a rewarded ad' (Credits economy → Reward rules)."
        ),
    )
    reward_description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="User-facing description of the reward.",
    )
    valid_hours = models.PositiveIntegerField(
        default=24,
        help_text="Extra downloads: how long the added allowance stays valid.",
    )

    # Limits
    cooldown_minutes = models.PositiveIntegerField(
        default=30, help_text="Minutes between rewarded ad views per user."
    )
    daily_limit_per_user = models.PositiveIntegerField(
        default=5, help_text="Maximum rewarded ads per user per day (0 = no daily cap)."
    )
    monthly_limit_per_user = models.PositiveIntegerField(
        default=30,
        help_text="Maximum rewarded ads per user per calendar month (0 = no monthly cap).",
    )

    # Watch requirement
    min_watch_seconds = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Seconds the server must see the user stay on the ad. 0 uses the site "
            "default (15 s). The server never accepts less than its floor."
        ),
    )

    class Meta:
        verbose_name = "Rewarded Ad Config"
        verbose_name_plural = "Rewarded Ad Configs"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_reward_type_display()})"

    def clean(self) -> None:
        limits: dict[str, tuple[int, int, str]] = {
            self.RewardType.DEVICE_RESET: (1, 3, "resets"),
            self.RewardType.DOWNLOAD_BONUS: (1, 50, "files"),
            self.RewardType.AD_FREE: (1, 72, "hours"),
        }
        if self.reward_type in limits:
            low, high, unit = limits[self.reward_type]
            if not low <= self.reward_amount <= high:
                raise ValidationError(
                    {"reward_amount": f"Use {low}-{high} {unit} for this reward."}
                )
        if self.is_enabled and self.placement_id is None:
            raise ValidationError(
                {"placement": "Choose a placement before enabling this rewarded ad."}
            )


class RewardedAdView(TimestampedModel):
    """
    One rewarded-ad watch: the server-side proof, the audit trail and the analytics row.

    Only the keyed digest of the single-use token is stored. ``accrued_seconds`` is
    measured by the server from heartbeat timestamps, never from client claims.
    """

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Skipped"
        ERROR = "error", "Error"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rewarded_ad_views",
    )
    config = models.ForeignKey(
        RewardedAdConfig, on_delete=models.SET_NULL, null=True, related_name="views"
    )
    token_digest = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )

    # View Details
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    heartbeat_count = models.PositiveIntegerField(default=0)
    early_attempts = models.PositiveSmallIntegerField(default=0)
    accrued_ms = models.PositiveBigIntegerField(
        default=0,
        help_text="Time the server saw the user on the ad, from heartbeat timestamps.",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    watch_duration_seconds = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.STARTED
    )

    # Reward
    reward_granted = models.BooleanField(default=False)
    reward_type = models.CharField(max_length=20, blank=True, default="")
    reward_amount = models.PositiveIntegerField(default=0)
    reward_note = models.CharField(max_length=255, blank=True, default="")

    # Abuse signals (impossible durations, too frequent, replays, too-early completion)
    flags = models.JSONField(default=list, blank=True)
    is_flagged = models.BooleanField(default=False, db_index=True)

    # Request Info
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    page_url = models.URLField(blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes = (
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["config", "status"]),
        )
        constraints = (
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="started"),
                name="ads_one_active_rewarded_view_per_user",
            ),
        )

    def __str__(self) -> str:
        return f"{self.user} - {self.config} @ {self.created_at}"


class RewardedAdGrant(TimestampedModel):
    """A time-boxed benefit earned by a rewarded ad that entitlements and ads read.

    Credits and slot resets are granted through the wallet and devices contracts and
    leave no row here; extra downloads and ad-free time live here until they expire.
    """

    class Kind(models.TextChoices):
        DOWNLOAD_BONUS = "download_bonus", "Extra downloads"
        AD_FREE = "ad_free", "Ad-free period"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rewarded_ad_grants",
    )
    view = models.OneToOneField(
        RewardedAdView, on_delete=models.CASCADE, related_name="grant"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.PositiveIntegerField(
        help_text="Files added to the allowance, or hours of ad-free time."
    )
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-expires_at"]
        indexes = (
            models.Index(
                fields=["user", "kind", "expires_at"], name="adgrant_active_idx"
            ),
        )

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for {self.user_id} until {self.expires_at}"


# ==================== AUTO ADS SCAN RESULTS ====================


class AutoAdsScanResult(TimestampedModel):
    """
    Stores results from automatic template scanning for ad placement discovery.

    The auto-ads system periodically scans templates to find optimal locations
    for ad placement based on content structure, user behavior, and revenue potential.
    """

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending Review"
        APPROVED = "approved", "Approved"
        PARTIAL = "partial", "Partially Approved"
        REJECTED = "rejected", "Rejected"
        EXCLUDED = "excluded", "Template Excluded"

    class TemplateType(models.TextChoices):
        PAGE = "page", "Static Page"
        LIST = "list", "List / Index"
        DETAIL = "detail", "Detail / Single"
        SEARCH = "search", "Search Results"
        UTILITY = "utility", "Utility / Form"
        COMPONENT = "component", "Component / Partial"

    class TrafficEstimate(models.TextChoices):
        HIGH = "high", "High Traffic"
        MEDIUM = "medium", "Medium Traffic"
        LOW = "low", "Low Traffic"

    template_path = models.CharField(max_length=255, db_index=True)
    suggested_placements = models.JSONField(
        default=list, help_text="List of suggested placement locations."
    )
    content_analysis = models.JSONField(
        default=dict,
        help_text="Analysis of content structure (headings, paragraphs, etc.).",
    )
    ai_recommendation = models.TextField(
        blank=True,
        default="",
        help_text="AI-generated recommendation for this template.",
    )
    score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="AI confidence score for recommendations.",
    )
    applied = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)

    # ── Enhanced scanner fields ──
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
        help_text="Admin review status for this scan result.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ads_reviewed_scans",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    template_type = models.CharField(
        max_length=20,
        choices=TemplateType.choices,
        default=TemplateType.PAGE,
        help_text="Auto-classified template type.",
    )
    estimated_traffic = models.CharField(
        max_length=10,
        choices=TrafficEstimate.choices,
        default=TrafficEstimate.MEDIUM,
        help_text="Estimated traffic level for this template.",
    )
    viewport_zones = models.JSONField(
        default=list,
        help_text="Detected viewport zones (above-fold, sidebar, mid-content, etc.).",
    )
    ad_density_warning = models.BooleanField(
        default=False,
        help_text="True if template already has high ad density.",
    )
    extends_chain = models.JSONField(
        default=list,
        help_text="Template inheritance chain (extends/includes).",
    )
    total_discoveries = models.PositiveIntegerField(
        default=0,
        help_text="Number of individual placement discoveries from this scan.",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        db_table = "ads_autoadsscanresult"
        verbose_name = "Auto-Ads Scan Result"
        verbose_name_plural = "Auto-Ads Scan Results"

    def __str__(self) -> str:
        return f"Scan: {self.template_path} ({self.created_at})"


class ScanDiscovery(TimestampedModel):
    """
    Individual ad placement discovery from a template scan.

    Each discovery represents a single potential ad zone found during scanning.
    Admin must approve discoveries before they become live AdPlacement records.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    scan_result = models.ForeignKey(
        AutoAdsScanResult,
        on_delete=models.CASCADE,
        related_name="discoveries",
    )
    placement_code = models.CharField(
        max_length=120,
        help_text="Proposed placement code (e.g. blog-post-detail-sidebar-top).",
    )
    placement_name = models.CharField(max_length=200)
    zone = models.CharField(
        max_length=60,
        blank=True,
        default="",
        help_text="Viewport zone: above-fold, mid-content, sidebar, footer, sticky.",
    )
    allowed_types = models.CharField(max_length=100, default="banner,native,html")
    allowed_sizes = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Confidence score 0–100 for this specific placement.",
    )
    rationale = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ads_reviewed_discoveries",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    placement = models.ForeignKey(
        "AdPlacement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ads_source_discovery",
        help_text="Created AdPlacement after approval.",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-confidence"]
        db_table = "ads_scandiscovery"
        verbose_name = "Scan Discovery"
        verbose_name_plural = "Scan Discoveries"

    def __str__(self) -> str:
        return f"{self.placement_code} ({self.get_status_display()})"


class TemplateAdExclusion(TimestampedModel):
    """
    Permanently excludes a template from ad placement scanning and serving.

    Admin can mark templates as ad-free. The scanner respects exclusions and
    skips these templates on future scans.
    """

    template_path = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Template path relative to templates/ root.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Why this template is excluded from ads.",
    )
    excluded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ads_template_exclusions",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["template_path"]
        db_table = "ads_templateadexclusion"
        verbose_name = "Template Ad Exclusion"
        verbose_name_plural = "Template Ad Exclusions"

    def __str__(self) -> str:
        return f"Excluded: {self.template_path}"


# ==================== AFFILIATE PRODUCTS ====================


class AffiliateProvider(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents an affiliate network provider with API integration.

    Supports multiple affiliate networks:
    - Amazon Associates (Product Advertising API)
    - CJ Affiliate (Commission Junction)
    - ShareASale
    - Rakuten Advertising
    - eBay Partner Network
    - AliExpress Affiliate
    - Banggood Affiliate
    - GearBest Affiliate
    - Custom/Direct partnerships
    """

    PROVIDER_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("amazon", "Amazon Associates"),
        ("cj", "CJ Affiliate"),
        ("shareasale", "ShareASale"),
        ("rakuten", "Rakuten Advertising"),
        ("ebay", "eBay Partner Network"),
        ("aliexpress", "AliExpress Affiliate"),
        ("banggood", "Banggood Affiliate"),
        ("gearbest", "GearBest Affiliate"),
        ("impact", "Impact Radius"),
        ("awin", "Awin"),
        ("custom", "Custom/Direct"),
    ]

    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=30, choices=PROVIDER_TYPES)
    is_enabled = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(
        default=10, help_text="Higher priority providers are used first."
    )

    # API Credentials
    # FINDING-0010: Store affiliate credentials as Fernet-encrypted ciphertext.
    # Never access these fields directly — use get_api_key() / set_api_key().
    api_key = models.TextField(
        blank=True, default="", help_text="Fernet-encrypted API key."
    )
    api_secret = models.TextField(
        blank=True, default="", help_text="Fernet-encrypted API secret."
    )

    # Tracking
    associate_tag = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Affiliate tracking tag (e.g., Amazon Associate Tag).",
    )
    partner_id = models.CharField(max_length=100, blank=True, default="")

    def set_api_key(self, plaintext: str) -> None:
        """Encrypt and store the API key."""
        if not plaintext:
            self.api_key = ""
            return
        try:
            from apps.firmwares.crypto import encrypt_password

            self.api_key = encrypt_password(plaintext)
        except _ENCRYPT_ERRORS:
            logger.warning("AffiliateProvider.set_api_key: encryption unavailable")
            self.api_key = ""

    def get_api_key(self) -> str:
        """Decrypt and return the API key, or empty string on failure."""
        if not self.api_key:
            return ""
        try:
            from apps.firmwares.crypto import decrypt_password

            return decrypt_password(self.api_key)
        except _DECRYPT_ERRORS:
            logger.warning(
                "AffiliateProvider.get_api_key: decryption failed (id=%s)", self.pk
            )
            return ""

    def set_api_secret(self, plaintext: str) -> None:
        """Encrypt and store the API secret."""
        if not plaintext:
            self.api_secret = ""
            return
        try:
            from apps.firmwares.crypto import encrypt_password

            self.api_secret = encrypt_password(plaintext)
        except _ENCRYPT_ERRORS:
            logger.warning("AffiliateProvider.set_api_secret: encryption unavailable")
            self.api_secret = ""

    def get_api_secret(self) -> str:
        """Decrypt and return the API secret, or empty string on failure."""
        if not self.api_secret:
            return ""
        try:
            from apps.firmwares.crypto import decrypt_password

            return decrypt_password(self.api_secret)
        except _DECRYPT_ERRORS:
            logger.warning(
                "AffiliateProvider.get_api_secret: decryption failed (id=%s)", self.pk
            )
            return ""

    # API Configuration
    api_endpoint = models.URLField(
        blank=True,
        default="",
        help_text="API base URL (auto-filled for known providers).",
    )
    api_region = models.CharField(
        max_length=20,
        blank=True,
        default="us-east-1",
        help_text="API region for Amazon/regional providers.",
    )
    marketplace = models.CharField(
        max_length=20,
        blank=True,
        default="www.amazon.com",
        help_text="Marketplace domain.",
    )

    # Tracking
    default_tracking_params = models.JSONField(
        default=dict,
        blank=True,
        help_text="Default UTM/tracking parameters for all links.",
    )

    # Revenue
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Average commission rate percentage.",
    )
    cookie_duration_days = models.PositiveIntegerField(
        default=24, help_text="Affiliate cookie duration in hours."
    )

    # Sync Status
    last_sync_at = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(max_length=100, blank=True, default="")
    products_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["-priority", "name"]
        verbose_name = "Affiliate Provider"
        verbose_name_plural = "Affiliate Providers"

    def __str__(self) -> str:
        return f"{self.name} ({self.get_provider_type_display()})"


class AffiliateProductCategory(TimestampedModel):
    """
    Categories for organizing affiliate products.
    Maps to device categories for automatic matching.
    """

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )

    # Mapping to device types
    device_keywords = models.TextField(
        blank=True,
        default="",
        help_text="Keywords to match devices (one per line). E.g., 'samsung', 'galaxy', 'phone'.",
    )
    brand_match = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Brand slug to auto-match (e.g., 'samsung', 'xiaomi').",
    )

    # Display
    icon = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Affiliate Product Categories"
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name


class AffiliateProduct(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents a product from an affiliate network.

    Products can be:
    - Auto-fetched via API (Amazon PA-API, etc.)
    - Manually added for specific campaigns
    - Matched to brands/models for contextual display
    """

    PRODUCT_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("accessory", "Phone Accessory"),
        ("case", "Phone Case"),
        ("charger", "Charger/Cable"),
        ("screen_protector", "Screen Protector"),
        ("battery", "Battery"),
        ("headphones", "Headphones/Earbuds"),
        ("smartwatch", "Smartwatch"),
        ("tool", "Repair Tool"),
        ("software", "Software/App"),
        ("book", "Book/Guide"),
        ("other", "Other"),
    ]

    provider = models.ForeignKey(
        AffiliateProvider, on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(
        AffiliateProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )

    # Product Info
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, db_index=True)
    description = models.TextField(blank=True, default="")
    product_type = models.CharField(
        max_length=30, choices=PRODUCT_TYPES, default="accessory"
    )

    # External IDs
    external_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Product ID from the affiliate network (ASIN, SKU, etc.).",
    )
    asin = models.CharField(max_length=20, blank=True, default="", db_index=True)
    upc = models.CharField(max_length=20, blank=True, default="")
    ean = models.CharField(max_length=20, blank=True, default="")

    # Pricing
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sale_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=5, default="USD")
    price_updated_at = models.DateTimeField(null=True, blank=True)

    # Images
    image_url = models.URLField(blank=True, default="")
    thumbnail_url = models.URLField(blank=True, default="")
    additional_images = models.JSONField(default=list, blank=True)

    # Links
    product_url = models.URLField(help_text="Direct product URL.")
    affiliate_url = models.URLField(
        blank=True, default="", help_text="Full affiliate link with tracking."
    )

    # Ratings
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    review_count = models.PositiveIntegerField(default=0)

    # Targeting - Which brands/models this product is relevant for
    target_brands = models.ManyToManyField(
        "firmwares.Brand",
        blank=True,
        related_name="affiliate_products",
        help_text="Show this product on these brand pages.",
    )
    target_models = models.ManyToManyField(
        "firmwares.Model",
        blank=True,
        related_name="affiliate_products",
        help_text="Show this product on these model pages.",
    )
    target_keywords = models.TextField(
        blank=True,
        default="",
        help_text="Keywords to match (one per line). E.g., 'samsung galaxy s24', 'xiaomi redmi'.",
    )

    # Universal product (shows on all pages)
    is_universal = models.BooleanField(
        default=False, help_text="Show on all pages regardless of targeting."
    )

    # Status
    is_enabled = models.BooleanField(default=True)
    is_in_stock = models.BooleanField(default=True)
    availability_message = models.CharField(max_length=100, blank=True, default="")

    # Performance
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    conversions = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # AI Optimization
    ai_relevance_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="AI-calculated relevance score for targeting.",
    )

    # Metadata from API
    api_data = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("provider", "external_id")
        ordering: ClassVar[list[str]] = ["-ai_relevance_score", "-rating", "name"]
        indexes = (
            models.Index(fields=["asin"]),
            models.Index(fields=["product_type", "is_enabled"]),
            models.Index(fields=["is_universal", "is_enabled"]),
        )

    def __str__(self) -> str:
        return f"{self.name} ({self.provider.name})"

    def get_display_price(self) -> object:
        """Return sale price if available, otherwise regular price."""
        if self.sale_price and self.sale_price < self.price:
            return self.sale_price
        return self.price

    def get_discount_percent(self):
        """Calculate discount percentage if on sale."""
        if self.sale_price and self.sale_price < self.price and self.price > 0:
            return int(((self.price - self.sale_price) / self.price) * 100)
        return 0


class AffiliateProductMatch(TimestampedModel):
    """
    Tracks which products are shown on which pages for analytics.
    Also stores manual overrides for product-to-page matching.
    """

    product = models.ForeignKey(
        AffiliateProduct, on_delete=models.CASCADE, related_name="matches"
    )

    # Match target (one of these)
    brand = models.ForeignKey(
        "firmwares.Brand",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_matches",
    )
    model = models.ForeignKey(
        "firmwares.Model",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_matches",
    )
    variant = models.ForeignKey(
        "firmwares.Variant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_matches",
    )
    blog_post = models.ForeignKey(
        "blog.Post",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="product_matches",
    )

    # Match quality
    match_type = models.CharField(
        max_length=20,
        choices=[
            ("auto", "Auto-matched"),
            ("manual", "Manually assigned"),
            ("ai", "AI recommended"),
        ],
        default="auto",
    )
    relevance_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Override settings
    position = models.PositiveIntegerField(
        default=0, help_text="Display order (0 = auto)."
    )
    is_pinned = models.BooleanField(
        default=False, help_text="Pinned products always show first."
    )
    is_hidden = models.BooleanField(
        default=False, help_text="Hide this product from this page."
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-is_pinned", "position", "-relevance_score"]

    def __str__(self):
        target = self.brand or self.model or self.variant or self.blog_post
        return f"{self.product.name} → {target}"


class AffiliateClick(TimestampedModel):
    """
    Track affiliate link clicks for analytics and fraud detection.
    """

    product = models.ForeignKey(
        AffiliateProduct,
        on_delete=models.SET_NULL,
        null=True,
        related_name="click_events",
    )
    provider = models.ForeignKey(
        AffiliateProvider,
        on_delete=models.SET_NULL,
        null=True,
        related_name="click_events",
    )

    # Source
    page_url = models.URLField()
    referrer = models.URLField(blank=True, default="")

    # Context
    brand_slug = models.CharField(max_length=100, blank=True, default="")
    model_slug = models.CharField(max_length=100, blank=True, default="")
    firmware_id = models.PositiveIntegerField(null=True, blank=True)
    blog_slug = models.CharField(max_length=200, blank=True, default="")

    # User
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    session_id = models.CharField(max_length=100, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")

    # Geolocation
    country = models.CharField(max_length=5, blank=True, default="")
    region = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes = (
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["provider", "created_at"]),
        )

    def __str__(self):
        return f"Click: {self.product} @ {self.created_at}"


class AdPlacement(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents an advertising slot or placement location in the UI.

    Ad placements are predetermined locations where ads can be served (e.g., sidebar,
    header, article sidebar). Each placement has configuration for allowed ad types,
    sizes, and context information used by the ad selection engine.

    These are typically auto-discovered by scan_ad_placements management command
    which parses templates for {% ad_block %} tags. Manual entries are also supported
    for programmatic placements.

    Attributes:
        name (CharField): Human-readable placement name (e.g., "Blog Sidebar Right").
        code (CharField): Stable programmatic identifier used in templates and discovery.
            Must be unique and consistent across deploys (e.g., "blog_sidebar_right").
        slug (SlugField): URL-friendly identifier derived from name.
        description (TextField): Human documentation of placement purpose and visibility.
        allowed_types (CharField): Comma-separated ad types this placement accepts
            (e.g., "banner,native,html"). Default allows all three main types.
        allowed_sizes (CharField): Comma-separated dimensions this placement accepts
            (e.g., "300x250,728x90"). Empty = all sizes allowed.
        context (CharField): Page context describing where placement appears
            (e.g., "blog_detail", "homepage"). Used for targeting.
        page_context (CharField): Alternative context field for page-specific targeting.
        template_reference (CharField): Full template path and line for documentation
            (e.g., "blog/includes/sidebar.html:line 45").
        is_enabled (BooleanField): Whether this placement should be used by the ad engine.
        is_active (BooleanField): Whether placement is currently active for serving.
        locked (BooleanField): If True, prevents auto-modification by scan_ad_placements.

    Examples:
        >>> # Create a blog sidebar placement
        >>> placement = AdPlacement.objects.create(
        ...     name="Blog Sidebar Right",
        ...     code="blog_sidebar_right",
        ...     slug="blog-sidebar-right",
        ...     description="Right sidebar on blog post pages",
        ...     allowed_types="banner,native",
        ...     allowed_sizes="300x250,300x600",
        ...     context="blog_detail"
        ... )

        >>> # Retrieve placement by code (typical usage in templates)
        >>> sidebar = AdPlacement.objects.get(code="blog_sidebar_right")
        >>> sidebar.name  # "Blog Sidebar Right"
    """

    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(
        max_length=120,
        unique=True,
        help_text="Stable placement code used in templates and auto-discovery.",
    )
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True, default="")
    allowed_types = models.CharField(
        max_length=100,
        blank=True,
        default="banner,native,html",
        help_text="Comma separated types",
    )
    allowed_sizes = models.CharField(
        max_length=100, blank=True, default="", help_text="e.g. 300x250,728x90"
    )
    context = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Page context e.g. blog_detail, blog_list, homepage",
    )
    page_context = models.CharField(max_length=100, blank=True, default="")
    template_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Template path this placement is limited to (e.g. blog/post_detail.html). "
            "Empty = the zone is filled on every page that declares it."
        ),
    )
    zone = models.CharField(
        max_length=30,
        blank=True,
        default="",
        choices=ZONE_CHOICES,
        help_text=(
            "Layout zone this placement fills. Pages declare zones with "
            "{% ad_zone %}; a placement without a zone is only served by code."
        ),
    )
    device_target = models.CharField(
        max_length=10,
        choices=DEVICE_CHOICES,
        default=DEVICE_ALL,
        help_text="Serve to every device, or only desktop or only mobile visitors.",
    )
    fallback_creative = models.ForeignKey(
        "AdCreative",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fallback_for_placements",
        help_text=(
            "Shown (with consent) when no assigned creative is eligible. "
            "Empty = the zone collapses and takes no space."
        ),
    )
    is_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    locked = models.BooleanField(
        default=False, help_text="Lock to prevent auto changes"
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["template_reference", "zone"],
                condition=Q(is_deleted=False) & ~Q(zone=""),
                name="ads_placement_one_per_template_zone",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Campaign(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents an advertising campaign with targeting rules and budget controls.

    A campaign is the container for ad creatives and defines how/when ads are served.
    Supports multiple ad network types (direct, affiliate, network, house) with flexible
    budget management and time-based scheduling.

    Attributes:
        name (CharField): Unique campaign identifier, human-readable name.
        is_active (BooleanField): Whether this campaign is active and eligible for serving.
        type (CharField): Campaign type - direct (internal), affiliate (partner),
            network (3rd party), or house (self-promotion).
        ad_network (CharField): Ad network identifier if applicable (e.g., AdSense, Google Ad Manager).
        budget (DecimalField): Total budget allocated for this campaign.
        daily_cap (PositiveIntegerField): Maximum impressions/clicks per day (0 = unlimited).
        total_cap (PositiveIntegerField): Maximum lifetime impressions/clicks (0 = unlimited).
        priority (IntegerField): Used for tie-breaking when multiple campaigns compete
            for the same placement. Higher values win ties.
        weight (PositiveIntegerField): Relative probability when selected (used in rotation).
        start_at (DateTimeField): Campaign start time. If set and future, campaign is inactive.
        end_at (DateTimeField): Campaign end time. If set and passed, campaign is inactive.
        targeting_rules (JSONField): Complex targeting logic (geo, device, user segment, etc.).
            Format: {"rule_type": {"operator": "and/or", "conditions": [...]}}.
        locked (BooleanField): If True, prevents automatic modification by optimization systems.

    Examples:
        >>> # Create a house ad campaign
        >>> campaign = Campaign.objects.create(
        ...     name="Q1 2025 Promotion",
        ...     type="house",
        ...     budget=5000.00,
        ...     priority=100,
        ...     start_at=timezone.now(),
        ...     end_at=timezone.now() + timedelta(days=90)
        ... )
        >>> campaign.is_live()  # Check if campaign is currently active
        True

        >>> # Affiliate campaign with daily cap
        >>> affiliate = Campaign.objects.create(
        ...     name="Amazon Associates Q1",
        ...     type="affiliate",
        ...     ad_network="amazon-associates",
        ...     daily_cap=1000,
        ...     priority=50
        ... )
    """

    name = models.CharField(max_length=150, unique=True)
    is_active = models.BooleanField(default=True)
    type = models.CharField(
        max_length=20,
        choices=[
            ("direct", "Direct"),
            ("affiliate", "Affiliate"),
            ("network", "Network"),
            ("house", "House"),
        ],
        default="direct",
    )
    ad_network = models.CharField(max_length=100, blank=True, default="")
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    daily_cap = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    total_cap = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    priority = models.IntegerField(default=0, help_text="Higher wins ties")
    weight = models.PositiveIntegerField(default=1)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    targeting_rules = models.JSONField(default=dict, blank=True)
    locked = models.BooleanField(default=False)
    promotion = models.ForeignKey(
        "promotions.Promotion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ad_campaigns",
        help_text="Optional offer this campaign promotes; it stops serving when the offer is not running.",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def offer_url(self) -> str:
        """Landing page of the promoted offer, empty when none is linked."""
        promotion = self.promotion
        return promotion.get_absolute_url() if promotion is not None else ""

    def is_live(self) -> bool:
        """
        Determine if this campaign is currently active and eligible for ad serving.

        A campaign is considered "live" if:
        1. is_active is True
        2. Either start_at is None or in the past (campaign has started)
        3. Either end_at is None or in the future (campaign hasn't ended)
        4. A linked promotion, if any, is running

        Returns:
            bool: True if campaign is active and within its scheduled window, False otherwise.

        Example:
            >>> campaign = Campaign.objects.get(name="Current Campaign")
            >>> if campaign.is_live():
            ...     # Safe to serve this campaign's ads
            ...     placement.choose_creative(campaign)
        """
        if not self.is_active:
            return False
        now = timezone.now()
        if self.start_at and self.start_at > now:
            return False
        if self.end_at and self.end_at < now:
            return False
        promotion = self.promotion
        return promotion is None or promotion.is_live


class AdCreative(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents the actual ad content served within campaigns.

    Creatives are the visual/content assets for ads - images, HTML, banners, native ads.
    Each creative belongs to a campaign and contains the actual content to be rendered.
    Multiple creatives can be rotated within a campaign via weight-based selection.

    Attributes:
        campaign (ForeignKey): Parent campaign this creative belongs to.
        name (CharField): Human-readable creative name (e.g., "Q1 Banner - Blue").
        creative_type (CharField): Type of creative - banner, native, html/js, or script.
            Determines rendering method and validation rules.
        html (TextField): HTML content for banner/native ad types. Includes markup and styling.
        html_code (TextField): Alternative HTML field (may be redundant with html).
        script_code (TextField): JavaScript code for script-type creatives.
        image_url (URLField): URL to image asset for banner ads.
        click_url (URLField): Destination URL when user clicks the ad.
        tracking_params (JSONField): UTM and custom tracking parameters appended to click_url.
            Format: {"utm_source": "site", "utm_campaign": "q1-promo", "custom": {...}}.
        weight (PositiveIntegerField): Relative probability in rotation scheme.
            If weight=2, this creative is 2x more likely than weight=1 creative.
        is_enabled (BooleanField): Whether this creative is available for serving.
        is_active (BooleanField): Whether this creative is currently active.
        locked (BooleanField): If True, prevents modification by optimization systems.

    Examples:
        >>> # Create a banner creative for a campaign
        >>> campaign = Campaign.objects.get(name="Q1 Promotion")
        >>> creative = AdCreative.objects.create(
        ...     campaign=campaign,
        ...     name="Banner - Blue Variant",
        ...     creative_type="banner",
        ...     image_url="https://cdn.example.com/ads/q1-blue.png",
        ...     click_url="https://example.com/q1-offer",
        ...     tracking_params={
        ...         "utm_source": "site",
        ...         "utm_campaign": "q1-promo",
        ...         "variant": "blue"
        ...     },
        ...     weight=2  # 2x more likely than other creatives
        ... )

        >>> # HTML/Rich media creative
        >>> html_creative = AdCreative.objects.create(
        ...     campaign=campaign,
        ...     name="Expandable HTML Ad",
        ...     creative_type="html",
        ...     html='''<div class="ad-container">
        ...         <h3>Special Offer</h3>
        ...         <button onclick="window.open(...)"Click Here</button>
        ...     </div>''',
        ...     click_url="https://example.com/offer",
        ...     weight=1
        ... )
    """

    CREATIVE_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("banner", "Banner"),
        ("native", "Native"),
        ("html", "HTML/JS"),
        ("script", "Script"),
    ]
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="creatives"
    )
    name = models.CharField(max_length=150)
    creative_type = models.CharField(max_length=20, choices=CREATIVE_TYPES)
    html = models.TextField(blank=True, default="")
    html_code = models.TextField(blank=True, default="")
    script_code = models.TextField(blank=True, default="")
    image_url = models.URLField(blank=True, default="")
    click_url = models.URLField(blank=True, default="")
    tracking_params = models.JSONField(default=dict, blank=True)
    weight = models.PositiveIntegerField(default=1)
    is_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    locked = models.BooleanField(default=False)

    class Meta:
        ordering: ClassVar[list[str]] = ["campaign", "name"]

    def __str__(self):
        return f"{self.campaign}: {self.name}"


class PlacementAssignment(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Maps creatives to placements, enabling flexible ad assignment.

    PlacementAssignment is the many-to-many relationship between AdPlacement and AdCreative,
    with additional configuration. This junction table allows:
    - One placement to serve multiple creatives (rotated via weight)
    - One creative to appear in multiple placements
    - Per-assignment weight and enable/disable controls
    - Soft delete support for audit trail

    The weight determines the relative probability of selection when multiple
    enabled assignments exist for a placement. During ad selection, one assignment
    is randomly chosen based on weight, then that creative is served.

    Attributes:
        placement (ForeignKey): The ad placement where creatives appear.
        creative (ForeignKey): The creative content to serve. Unique together with placement.
        weight (PositiveIntegerField): Relative probability in rotation.
            If placement has two assignments with weight 1 and 2, the second is 2x
            more likely to be selected. Default 1 means equal probability.
        is_enabled (BooleanField): Whether this specific assignment is active.
        is_active (BooleanField): Whether this assignment is currently in use.
        locked (BooleanField): If True, prevents modification by optimization systems.

    Examples:
        >>> placement = AdPlacement.objects.get(code="blog_sidebar")
        >>> creative1 = AdCreative.objects.get(name="Banner A")
        >>> creative2 = AdCreative.objects.get(name="Banner B")
        >>>
        >>> # Assign creative to placement with weight 2 (more likely)
        >>> assign1 = PlacementAssignment.objects.create(
        ...     placement=placement,
        ...     creative=creative1,
        ...     weight=2  # 2x more likely than creative2
        ... )
        >>>
        >>> # Assign second creative with default weight
        >>> assign2 = PlacementAssignment.objects.create(
        ...     placement=placement,
        ...     creative=creative2,
        ...     weight=1
        ... )
        >>>
        >>> # When rendering, picker will select creative1 roughly 2/3 of time
        >>> # See: apps.ads.services.rotation for selection logic
    """

    placement = models.ForeignKey(
        AdPlacement, on_delete=models.CASCADE, related_name="assignments"
    )
    creative = models.ForeignKey(
        AdCreative, on_delete=models.CASCADE, related_name="assignments"
    )
    weight = models.PositiveIntegerField(default=1)
    is_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    locked = models.BooleanField(default=False)

    class Meta:
        unique_together = ("placement", "creative")

    def __str__(self):
        return f"{self.placement} -> {self.creative}"


class AffiliateSource(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents an affiliate network or partner program.

    AffiliateSource is the parent entity for affiliate links. It stores configuration
    for a specific affiliate network (e.g., Amazon Associates, CJ Affiliate) including
    base URL, authentication details, and common tracking parameters.

    Attributes:
        name (CharField): Unique affiliate network name (e.g., "Amazon Associates").
        network (CharField): Network identifier (e.g., "amazon", "cj", "rakuten").
        base_url (URLField): Base URL for the affiliate network (used for link construction).
        is_enabled (BooleanField): Whether this network is active for new link creation.
        locked (BooleanField): If True, prevents modification by optimization systems.
        tracking_parameters (JSONField): Default tracking parameters appended to all links
            from this network. Format: {"utm_source": "affiliate", "network": "amazon", ...}.
        metadata (JSONField): Additional configuration data (API keys, account IDs, etc.).
            Use environment variables for sensitive data rather than storing here.

    Examples:
        >>> # Create Amazon Associates source
        >>> amazon = AffiliateSource.objects.create(
        ...     name="Amazon Associates",
        ...     network="amazon",
        ...     base_url="https://amazon.com",
        ...     tracking_parameters={
        ...         "utm_source": "myblog",
        ...         "utm_medium": "affiliate",
        ...         "utm_campaign": "product-reviews"
        ...     }
        ... )
        >>>
        >>> # Create link from this source
        >>> link = AffiliateLink.objects.create(
        ...     source=amazon,
        ...     name="PHP Best Practices Book",
        ...     target_url="https://amazon.com/dp/B00NPXMX...",
        ...     affiliate_url="https://amazon.com/dp/B00NPXMX...?tag=myblog-20"
        ... )
    """

    name = models.CharField(max_length=100, unique=True)
    network = models.CharField(max_length=100, blank=True, default="")
    base_url = models.URLField(blank=True, default="")
    is_enabled = models.BooleanField(default=True)
    locked = models.BooleanField(default=False)
    tracking_parameters = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class AffiliateLink(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Represents a specific affiliate link within a partner program.

    AffiliateLink stores a single monetized link (product, landing page, etc.)
    within an affiliate network. It tracks the original URL, the affiliate-tagged URL,
    usage statistics, and associated tags for easy discovery and analytics.

    Typical usage: create links for products/content you want to monetize, then
    embed them in blog posts, articles, or distribute via social media.

    Attributes:
        source (ForeignKey): Parent AffiliateSource (network) this link belongs to.
        name (CharField): Human-readable link description (e.g., "Django for Beginners Book").
        target_url (URLField): Original destination URL (the product/landing page).
        affiliate_url (URLField): The monetized URL with tracking parameters and tags.
            This is what gets displayed/clicked by users.
        slug (SlugField): URL-friendly identifier for tracking in analytics. Indexed for quick lookup.
        tags (ManyToManyField): Tags for categorization (e.g., "python", "books", "courses").
        usage_count (PositiveIntegerField): Count of how many times this link was clicked/viewed.
        last_used_at (DateTimeField): Timestamp of most recent click/display.
        url (URLField): Primary URL field (may duplicate affiliate_url).
        is_enabled (BooleanField): Whether link is active for embedding/display.
        is_active (BooleanField): Whether link is currently in use.
        locked (BooleanField): If True, prevents modification by optimization systems.

    Meta:
        unique_together: (source, name) - one link name per source.

    Examples:
        >>> source = AffiliateSource.objects.get(name="Amazon Associates")
        >>>
        >>> # Create an affiliate link
        >>> link = AffiliateLink.objects.create(
        ...     source=source,
        ...     name="Django for Beginners",
        ...     target_url="https://amazon.com/dp/B00NPM5T04",
        ...     affiliate_url="https://amazon.com/dp/B00NPM5T04?tag=myblog-20",
        ...     slug="django-for-beginners",
        ...     url="https://amazon.com/dp/B00NPM5T04?tag=myblog-20"
        ... )
        >>>
        >>> # Add tags for categorization
        >>> link.tags.add("python", "django", "books")
        >>>
        >>> # Track usage
        >>> link.usage_count += 1
        >>> link.last_used_at = timezone.now()
        >>> link.save()
        >>>
        >>> # Find all Python book links
        >>> python_links = AffiliateLink.objects.filter(
        ...     tags__name="python",
        ...     is_enabled=True
        ... ).distinct()
    """

    source = models.ForeignKey(
        AffiliateSource, on_delete=models.CASCADE, related_name="links"
    )
    name = models.CharField(max_length=150)
    target_url = models.URLField(blank=True, default="")
    affiliate_url = models.URLField(blank=True, default="")
    slug = models.SlugField(max_length=180, blank=True, db_index=True)
    tags = models.ManyToManyField(
        "tags.Tag", blank=True, related_name="affiliate_links"
    )
    usage_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    url = models.URLField()
    is_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    locked = models.BooleanField(default=False)

    class Meta:
        unique_together = ("source", "name")

    def __str__(self):
        return f"{self.source} - {self.name}"


class AdEvent(TimestampedModel):
    """
    Tracks advertising events (impressions, clicks) for analytics and attribution.

    AdEvent is the core analytics table for the ads system. Every time an ad is
    rendered (impression) or clicked, a record is created for later analysis.
    This data powers revenue tracking, performance analysis, and fraud detection.

    Note: High-volume events may warrant offloading to an analytics warehouse
    or time-series database for performance. Consider periodic archival of old records.

    Attributes:
        event_type (CharField): "impression" or "click". Determines what action occurred.
        placement (ForeignKey): Which placement served this ad (null if deleted).
        creative (ForeignKey): Which creative was served (null if deleted).
        campaign (ForeignKey): Which campaign this event belongs to (null if deleted).
        user (ForeignKey): Which user triggered this event (null for anonymous).
        request_meta (JSONField): HTTP request metadata useful for analytics
            (referrer, user_agent excerpt, IP geo, etc.).
        page_url (URLField): URL of page where ad appeared.
        referrer_url (URLField): HTTP Referer header value.
        user_agent (TextField): Full User-Agent string for device/browser detection.
        session_id (CharField): Session identifier for grouping user actions.
        site_domain (CharField): Which domain served this event (multi-domain support).
        consent_granted (BooleanField): Whether user had consented to tracking
            (GDPR/privacy compliance marker).

    Meta:
        indexes: (event_type, created_at) for time-series queries;
                 (campaign) for campaign-specific reports.

    Examples:
        >>> # Log an impression event
        >>> from django.utils import timezone
        >>> AdEvent.objects.create(
        ...     event_type="impression",
        ...     placement=placement,
        ...     creative=creative,
        ...     campaign=campaign,
        ...     user=user,
        ...     page_url="https://example.com/blog/article",
        ...     referrer_url="https://google.com/search?q=...",
        ...     user_agent=request.META.get("HTTP_USER_AGENT"),
        ...     session_id=request.session.session_key,
        ...     site_domain="example.com",
        ...     consent_granted=user_consent.has_analytics
        ... )
        >>>
        >>> # Analyze impressions by day for a campaign
        >>> from django.db.models import Count
        >>> daily_stats = AdEvent.objects.filter(
        ...     event_type="impression",
        ...     campaign__name="Q1 Promotion"
        ... ).extra(
        ...     select={"day": "DATE(created_at)"}
        ... ).values("day").annotate(count=Count("id"))
        >>>
        >>> # Calculate CTR (Click-Through Rate)
        >>> impressions = AdEvent.objects.filter(
        ...     event_type="impression",
        ...     creative=creative
        ... ).count()
        >>> clicks = AdEvent.objects.filter(
        ...     event_type="click",
        ...     creative=creative
        ... ).count()
        >>> ctr = (clicks / impressions * 100) if impressions > 0 else 0
    """

    EVENT_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("impression", "Impression"),
        ("click", "Click"),
    ]
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    placement = models.ForeignKey(
        AdPlacement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    creative = models.ForeignKey(
        AdCreative,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ad_events",
    )
    request_meta = models.JSONField(default=dict, blank=True)
    page_url = models.URLField(blank=True, default="")
    referrer_url = models.URLField(blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    session_id = models.CharField(max_length=128, blank=True, default="")
    site_domain = models.CharField(max_length=100, blank=True, default="")
    consent_granted = models.BooleanField(
        default=False, help_text="True if user consented to ads tracking."
    )

    class Meta:
        indexes = (
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["campaign"]),
        )

    def __str__(self):
        return f"{self.event_type} @ {self.created_at}"


class AdsSettings(SingletonModel):
    """
    Per-app configuration singleton for the Ads system.

    AdsSettings is a singleton model (only one instance ever exists) that stores
    global configuration for the ads application. This allows the app to be
    reused independently in other projects with different settings.

    Since it's a singleton, always access via: AdsSettings.objects.get_solo()
    Never try to create multiple instances.

    Supports:
    - Multiple ad networks (AdSense, Media.net, Ezoic, AdThrive, etc.)
    - Auto ads (page-level ads that automatically place themselves)
    - Rewarded ads (video ads users watch for rewards)
    - Native ads, in-feed ads, in-article ads
    - Header bidding integration
    - AI-powered optimization
    """

    # ==================== MASTER CONTROLS ====================
    ads_enabled = models.BooleanField(
        default=False, help_text="Master switch to completely disable all ad serving."
    )
    affiliate_enabled = models.BooleanField(
        default=False, help_text="Enable/disable affiliate link functionality."
    )
    ad_networks_enabled = models.BooleanField(
        default=False, help_text="Enable/disable third-party ad networks."
    )
    ad_aggressiveness_level = models.CharField(
        max_length=20,
        choices=[
            ("minimal", "Minimal - Few ads, best UX"),
            ("balanced", "Balanced - Recommended"),
            ("aggressive", "Aggressive - Maximum revenue"),
        ],
        default="balanced",
    )

    # ==================== AUTO ADS CONFIGURATION ====================
    auto_ads_enabled = models.BooleanField(
        default=False,
        help_text="Enable AI-powered automatic ad placement (page-level ads).",
    )
    auto_ads_scan_interval = models.PositiveIntegerField(
        default=24,
        help_text="Hours between automatic template scans for new placements.",
    )
    auto_ads_in_article = models.BooleanField(
        default=True, help_text="Allow auto-insertion of ads within article content."
    )
    auto_ads_in_feed = models.BooleanField(
        default=True, help_text="Allow auto-insertion of ads in list/feed pages."
    )
    auto_ads_anchor = models.BooleanField(
        default=False, help_text="Enable anchor/sticky ads at page bottom."
    )
    auto_ads_vignette = models.BooleanField(
        default=False, help_text="Enable full-screen vignette ads between pages."
    )
    auto_ads_sidebar = models.BooleanField(
        default=True, help_text="Auto-fill sidebar/rail ad slots."
    )

    # ==================== REWARDED ADS ====================
    rewarded_ads_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Master switch for rewarded ads. Each reward (credits, device reset, "
            "extra downloads, ad-free) is configured per Rewarded Ad Config."
        ),
    )

    # ==================== AD NETWORK CONFIGURATIONS ====================
    # Google AdSense
    adsense_enabled = models.BooleanField(
        default=False, help_text="Enable Google AdSense."
    )
    adsense_publisher_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="AdSense Publisher ID (ca-pub-XXXXXXXXXXXXXXXX).",
    )
    adsense_auto_ads = models.BooleanField(
        default=False, help_text="Enable AdSense Auto Ads (page-level ads)."
    )

    # Media.net (Yahoo/Bing Network)
    medianet_enabled = models.BooleanField(
        default=False, help_text="Enable Media.net ads."
    )
    medianet_customer_id = models.CharField(max_length=50, blank=True, default="")

    # Ezoic
    ezoic_enabled = models.BooleanField(
        default=False, help_text="Enable Ezoic AI ad optimization."
    )
    ezoic_site_id = models.CharField(max_length=50, blank=True, default="")

    # AdThrive / Mediavine (Premium publishers)
    adthrive_enabled = models.BooleanField(
        default=False, help_text="Enable AdThrive (premium)."
    )
    adthrive_site_id = models.CharField(max_length=50, blank=True, default="")
    mediavine_enabled = models.BooleanField(
        default=False, help_text="Enable Mediavine (premium)."
    )
    mediavine_site_id = models.CharField(max_length=50, blank=True, default="")

    # PropellerAds (Push notifications, popunders)
    propellerads_enabled = models.BooleanField(
        default=False, help_text="Enable PropellerAds."
    )
    propellerads_zone_id = models.CharField(max_length=50, blank=True, default="")

    # Amazon Publisher Services
    amazon_ads_enabled = models.BooleanField(
        default=False, help_text="Enable Amazon Publisher Services."
    )
    amazon_ads_publisher_id = models.CharField(max_length=100, blank=True, default="")

    # Infolinks (In-text ads)
    infolinks_enabled = models.BooleanField(
        default=False, help_text="Enable Infolinks in-text ads."
    )
    infolinks_pid = models.CharField(max_length=50, blank=True, default="")

    # Carbon Ads (Developer-focused)
    carbon_enabled = models.BooleanField(
        default=False, help_text="Enable Carbon Ads (tech audience)."
    )
    carbon_serve_id = models.CharField(max_length=100, blank=True, default="")

    # BuySellAds
    buysellads_enabled = models.BooleanField(
        default=False, help_text="Enable BuySellAds."
    )
    buysellads_zone_id = models.CharField(max_length=100, blank=True, default="")

    # ==================== HEADER BIDDING ====================
    header_bidding_enabled = models.BooleanField(
        default=False, help_text="Enable Prebid.js header bidding for maximum revenue."
    )
    prebid_timeout_ms = models.PositiveIntegerField(
        default=1000, help_text="Timeout for header bidding auctions in milliseconds."
    )
    prebid_bidders = models.JSONField(
        default=list,
        blank=True,
        help_text="List of enabled Prebid bidder configurations.",
    )

    # ==================== NATIVE ADS ====================
    native_ads_enabled = models.BooleanField(
        default=False, help_text="Enable native ad formats that match site design."
    )
    native_ads_style = models.CharField(
        max_length=20,
        choices=[
            ("card", "Card Style"),
            ("inline", "Inline/In-Feed"),
            ("recommendation", "Recommendation Widget"),
        ],
        default="card",
    )

    # ==================== AI OPTIMIZATION ====================
    ai_optimization_enabled = models.BooleanField(
        default=False,
        help_text="Enable AI-powered ad placement and creative optimization.",
    )
    ai_model_provider = models.CharField(
        max_length=50,
        choices=[
            ("internal", "Internal Heuristics"),
            ("openai", "OpenAI GPT"),
            ("anthropic", "Anthropic Claude"),
            ("custom", "Custom Model"),
        ],
        default="internal",
    )
    ai_optimize_placements = models.BooleanField(
        default=True, help_text="AI suggests optimal placement locations."
    )
    ai_optimize_creatives = models.BooleanField(
        default=True, help_text="AI optimizes creative selection based on performance."
    )
    ai_optimize_bidding = models.BooleanField(
        default=False, help_text="AI adjusts floor prices and bidding strategies."
    )

    # Consent is owned by apps.consent: ads are measured and personalised only after
    # the visitor allows the "advertising" category (see apps.consent.services).

    # ==================== FREQUENCY & LIMITS ====================
    max_ads_per_page = models.PositiveIntegerField(
        default=5, help_text="Maximum number of ads to show per page."
    )
    min_content_between_ads = models.PositiveIntegerField(
        default=300, help_text="Minimum words/pixels of content between in-article ads."
    )
    refresh_interval_seconds = models.PositiveIntegerField(
        default=0, help_text="Auto-refresh ads after X seconds (0=disabled)."
    )
    lazy_load_ads = models.BooleanField(
        default=True, help_text="Lazy load ads for better page performance."
    )

    # ==================== EXCLUSIONS ====================
    excluded_pages = models.TextField(
        blank=True,
        default="",
        help_text="URL patterns to exclude from ads (one per line, supports wildcards).",
    )
    excluded_categories = models.TextField(
        blank=True,
        default="",
        help_text="Categories/tags to exclude from ads (one per line).",
    )
    excluded_user_roles = models.TextField(
        blank=True,
        default="staff,superuser",
        help_text="User roles that see no ads (comma-separated).",
    )

    # ==================== AFFILIATE PRODUCTS ====================
    affiliate_products_enabled = models.BooleanField(
        default=False, help_text="Enable automated affiliate product recommendations."
    )
    affiliate_products_max_per_page = models.PositiveIntegerField(
        default=4, help_text="Maximum affiliate product widgets per page."
    )
    affiliate_products_cache_hours = models.PositiveIntegerField(
        default=6, help_text="Hours to cache product data from affiliate APIs."
    )
    affiliate_products_auto_fetch = models.BooleanField(
        default=True,
        help_text="Automatically fetch products from APIs based on page context.",
    )
    affiliate_products_show_on_firmware = models.BooleanField(
        default=True, help_text="Show affiliate products on firmware detail pages."
    )
    affiliate_products_show_on_model = models.BooleanField(
        default=True, help_text="Show affiliate products on device model pages."
    )
    affiliate_products_show_on_brand = models.BooleanField(
        default=True, help_text="Show affiliate products on brand pages."
    )
    affiliate_products_show_on_blog = models.BooleanField(
        default=True, help_text="Show affiliate products on blog posts."
    )
    affiliate_products_fallback_keywords = models.TextField(
        blank=True,
        default="phone accessories,smartphone case,screen protector,charger,cable",
        help_text="Default keywords to search when no specific context available.",
    )

    # Amazon Product Advertising API
    amazon_paapi_enabled = models.BooleanField(
        default=False, help_text="Enable Amazon Product Advertising API integration."
    )
    amazon_paapi_access_key = models.CharField(
        max_length=100, blank=True, default="", help_text="Amazon PA-API Access Key ID."
    )
    amazon_paapi_secret_key = models.CharField(
        max_length=100, blank=True, default="", help_text="Amazon PA-API Secret Key."
    )
    amazon_paapi_partner_tag = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="Amazon Associate Partner/Tracking Tag (e.g., mysite-20).",
    )
    amazon_paapi_region = models.CharField(
        max_length=20,
        choices=[
            ("us-east-1", "US (amazon.com)"),
            ("eu-west-1", "UK (amazon.co.uk)"),
            ("us-west-2", "CA (amazon.ca)"),
            ("eu-west-1", "DE (amazon.de)"),
            ("eu-west-1", "FR (amazon.fr)"),
            ("eu-west-1", "IT (amazon.it)"),
            ("eu-west-1", "ES (amazon.es)"),
            ("us-west-2", "MX (amazon.com.mx)"),
            ("us-west-2", "BR (amazon.com.br)"),
            ("ap-northeast-1", "JP (amazon.co.jp)"),
            ("ap-southeast-1", "IN (amazon.in)"),
            ("ap-southeast-2", "AU (amazon.com.au)"),
        ],
        default="us-east-1",
        help_text="Amazon marketplace region.",
    )
    amazon_paapi_marketplace = models.CharField(
        max_length=30,
        choices=[
            ("www.amazon.com", "United States"),
            ("www.amazon.co.uk", "United Kingdom"),
            ("www.amazon.ca", "Canada"),
            ("www.amazon.de", "Germany"),
            ("www.amazon.fr", "France"),
            ("www.amazon.it", "Italy"),
            ("www.amazon.es", "Spain"),
            ("www.amazon.com.mx", "Mexico"),
            ("www.amazon.com.br", "Brazil"),
            ("www.amazon.co.jp", "Japan"),
            ("www.amazon.in", "India"),
            ("www.amazon.com.au", "Australia"),
        ],
        default="www.amazon.com",
        help_text="Amazon marketplace domain.",
    )

    # ==================== METADATA ====================
    custom_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Custom JSON configuration for future extensibility.",
    )

    class Meta:
        verbose_name = "Ads Settings"

    def __str__(self) -> str:
        return "Ads Settings"

    def get_enabled_networks(self) -> list:
        """Return list of enabled ad network identifiers."""
        networks = []
        if self.adsense_enabled:
            networks.append("adsense")
        if self.medianet_enabled:
            networks.append("medianet")
        if self.ezoic_enabled:
            networks.append("ezoic")
        if self.adthrive_enabled:
            networks.append("adthrive")
        if self.mediavine_enabled:
            networks.append("mediavine")
        if self.propellerads_enabled:
            networks.append("propellerads")
        if self.amazon_ads_enabled:
            networks.append("amazon")
        if self.infolinks_enabled:
            networks.append("infolinks")
        if self.carbon_enabled:
            networks.append("carbon")
        if self.buysellads_enabled:
            networks.append("buysellads")
        return networks

    def should_show_ads(self, user=None, page_url="") -> bool:
        """Check if ads should be shown for this user/page combination."""
        if not self.ads_enabled:
            return False

        if user and user.is_authenticated:
            from apps.ads.rewarded import user_is_ad_free

            if user_is_ad_free(user):
                return False

        # Check excluded user roles
        if user and user.is_authenticated:
            excluded_roles = [r.strip() for r in self.excluded_user_roles.split(",")]
            if "staff" in excluded_roles and user.is_staff:
                return False
            if "superuser" in excluded_roles and user.is_superuser:
                return False

        # Check excluded pages
        if page_url and self.excluded_pages:
            import fnmatch

            for pattern in self.excluded_pages.strip().split("\n"):
                pattern = pattern.strip()
                if pattern and fnmatch.fnmatch(page_url, pattern):
                    return False

        return True
