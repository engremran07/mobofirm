from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from solo.models import SingletonModel

from apps.core.models import AuditFieldsModel, SoftDeleteModel, TimestampedModel

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise


class SEOModel(TimestampedModel, SoftDeleteModel, AuditFieldsModel):
    """
    Generic container for SEO-related data tied to any model instance.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    locked = models.BooleanField(default=False)
    ai_generated = models.BooleanField(default=False)

    class Meta:
        unique_together = ("content_type", "object_id")

    def __str__(self) -> str:
        return f"SEO:{self.content_type}#{self.object_id}"


class Metadata(TimestampedModel):
    seo = models.OneToOneField(
        SEOModel, on_delete=models.CASCADE, related_name="metadata"
    )
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.CharField(max_length=320, blank=True, default="")
    focus_keywords = models.JSONField(default=list, blank=True)
    canonical_url = models.URLField(blank=True, default="")
    robots_directives = models.CharField(max_length=255, blank=True, default="")
    social_title = models.CharField(max_length=255, blank=True, default="")
    social_description = models.CharField(max_length=320, blank=True, default="")
    social_image = models.URLField(blank=True, default="")
    noindex = models.BooleanField(default=False)
    nofollow = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.meta_title or f"Metadata for {self.seo}"


class SitemapEntry(TimestampedModel):
    url = models.URLField(unique=True)
    lastmod = models.DateTimeField(null=True, blank=True)
    changefreq = models.CharField(max_length=20, blank=True, default="")
    priority = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    is_active = models.BooleanField(default=True)
    last_status = models.PositiveIntegerField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return self.url


class Redirect(TimestampedModel):
    source = models.CharField(max_length=255, unique=True)
    target = models.CharField(max_length=255)
    is_permanent = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["source"]

    def __str__(self) -> str:
        return f"{self.source} → {self.target}"

    def clean(self) -> None:
        super().clean()
        from .redirects import validate_redirect

        errors = validate_redirect(self.source, self.target, exclude_pk=self.pk)
        if errors:
            raise ValidationError({"target": errors})

    def save(self, *args, **kwargs):
        self.full_clean(validate_unique=False)
        super().save(*args, **kwargs)


class LinkableEntity(TimestampedModel):
    """
    Registry of linkable content for internal linking.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True, db_index=True)
    aliases = models.JSONField(default=list, blank=True)
    entity_type = models.CharField(max_length=100, blank=True, default="")
    url = models.URLField()
    keywords = models.JSONField(default=list, blank=True)
    embedding = models.BinaryField(null=True, blank=True)
    vector = models.BinaryField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("content_type", "object_id")
        indexes = (models.Index(fields=["content_type", "object_id"]),)

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:240]
            self.slug = base or f"entity-{self.pk or ''}"
        super().save(*args, **kwargs)


class LinkSuggestion(TimestampedModel):
    """
    Stable suggestions for internal linking. Not auto-applied unless requested.
    """

    source = models.ForeignKey(
        LinkableEntity, on_delete=models.CASCADE, related_name="suggestions_from"
    )
    target = models.ForeignKey(
        LinkableEntity, on_delete=models.CASCADE, related_name="suggestions_to"
    )
    score = models.FloatField(default=0.0)
    is_applied = models.BooleanField(default=False)
    locked = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("source", "target")

    def __str__(self) -> str:
        return f"{self.source} → {self.target} ({self.score:.2f})"


class SEOSettings(SingletonModel):
    """Single settings authority for the SEO app (read via ``apps.seo.api``).

    Secrets (search-engine API keys and tokens) are never stored here; adapters read
    them from environment-backed Django settings.
    """

    class AutonomyMode(models.TextChoices):
        OFF = "off", "Off - no automation runs"
        SUGGEST = "suggest", "Suggest - propose changes, apply nothing"
        SAFE_AUTOPILOT = "safe_autopilot", "Safe autopilot - apply low-risk changes"
        FULL = "full", "Full - apply all except protected policies"

    seo_enabled = models.BooleanField(default=True)
    sitemap_enabled = models.BooleanField(default=True)
    auto_meta_enabled = models.BooleanField(
        default=True,
        help_text="Derive titles and descriptions from real page data when no "
        "explicit value exists.",
    )
    auto_schema_enabled = models.BooleanField(
        default=True, help_text="Emit JSON-LD structured data."
    )
    auto_linking_enabled = models.BooleanField(default=False)

    site_name = models.CharField(
        max_length=80, blank=True, default="", help_text="Falls back to SITE_NAME."
    )
    title_separator = models.CharField(max_length=8, default=" | ")
    default_description = models.CharField(max_length=320, blank=True, default="")
    default_social_image = models.URLField(blank=True, default="")
    organization_logo = models.URLField(blank=True, default="")
    twitter_site = models.CharField(
        max_length=32, blank=True, default="", help_text="Handle without the @."
    )

    min_firmware_for_model_index = models.PositiveSmallIntegerField(
        default=1,
        help_text="Model pages with fewer public firmware files are noindex.",
    )
    min_models_for_brand_index = models.PositiveSmallIntegerField(default=1)
    min_models_for_chipset_index = models.PositiveSmallIntegerField(default=1)
    min_posts_for_tag_index = models.PositiveSmallIntegerField(default=1)

    allow_ai_search_bots = models.BooleanField(
        default=True, help_text="OAI-SearchBot, PerplexityBot, Claude-SearchBot."
    )
    allow_ai_user_agents = models.BooleanField(
        default=True, help_text="ChatGPT-User, Claude-User, Perplexity-User."
    )
    allow_ai_training_bots = models.BooleanField(
        default=False,
        help_text="GPTBot, ClaudeBot, Google-Extended, Applebot-Extended, CCBot.",
    )

    autonomy_mode = models.CharField(
        max_length=20, choices=AutonomyMode.choices, default=AutonomyMode.SUGGEST
    )
    crawl_max_pages = models.PositiveIntegerField(
        default=300, validators=[MinValueValidator(1), MaxValueValidator(5000)]
    )
    crawl_delay_ms = models.PositiveIntegerField(
        default=250, validators=[MaxValueValidator(10000)]
    )

    indexnow_enabled = models.BooleanField(default=False)
    indexnow_key = models.CharField(max_length=64, blank=True, default="")
    search_console_enabled = models.BooleanField(default=False)
    pagespeed_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = "SEO Settings"

    def __str__(self) -> str:
        return "SEO Settings"

    def clean(self) -> None:
        super().clean()
        if self.indexnow_key and not (
            8 <= len(self.indexnow_key) <= 64
            and self.indexnow_key.replace("-", "").isalnum()
        ):
            raise ValidationError(
                {"indexnow_key": "Use 8-64 letters, digits or hyphens."}
            )
        if self.indexnow_enabled and not self.indexnow_key:
            raise ValidationError(
                {"indexnow_key": "A key is required to enable IndexNow."}
            )


class SeoKeywordHistory(TimestampedModel):
    class Intent(models.TextChoices):
        INFORMATIONAL = "informational", "Informational"
        TRANSACTIONAL = "transactional", "Transactional"
        NAVIGATIONAL = "navigational", "Navigational"
        COMMERCIAL = "commercial", "Commercial"
        UNKNOWN = "unknown", "Unknown"

    keyword = models.CharField(max_length=255, db_index=True)
    target_url = models.URLField(blank=True, default="")
    position = models.PositiveIntegerField(null=True, blank=True)
    search_volume = models.PositiveIntegerField(default=0)
    intent = models.CharField(
        max_length=20, choices=Intent.choices, default=Intent.UNKNOWN
    )
    source = models.CharField(max_length=100, blank=True, default="manual")

    class Meta:
        db_table = "seo_seokeywordhistory"
        verbose_name = "SEO Keyword History"
        verbose_name_plural = "SEO Keyword History"
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.keyword} ({self.intent})"


class SeoEntity(TimestampedModel):
    name = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=255, unique=True)
    entity_type = models.CharField(max_length=100, blank=True, default="")
    attributes = models.JSONField(default=dict, blank=True)
    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seo_entities",
    )
    source_object_id = models.CharField(max_length=64, blank=True, default="")
    source_object = GenericForeignKey("source_content_type", "source_object_id")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "seo_seoentity"
        verbose_name = "SEO Entity"
        verbose_name_plural = "SEO Entities"
        ordering: ClassVar[list[str]] = ["name"]
        indexes = (models.Index(fields=["entity_type", "is_active"]),)

    def __str__(self) -> str:
        return self.name


class SeoEntityEdge(TimestampedModel):
    source = models.ForeignKey(
        SeoEntity, on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    target = models.ForeignKey(
        SeoEntity, on_delete=models.CASCADE, related_name="incoming_edges"
    )
    relation_type = models.CharField(max_length=100, db_index=True)
    weight = models.FloatField(default=0.0)

    class Meta:
        db_table = "seo_seoentityedge"
        verbose_name = "SEO Entity Edge"
        verbose_name_plural = "SEO Entity Edges"
        ordering: ClassVar[list[str]] = ["-weight", "-created_at"]
        unique_together = ("source", "target", "relation_type")

    def __str__(self) -> str:
        return f"{self.source} -> {self.target} [{self.relation_type}]"


class InterlinkExclusion(TimestampedModel):
    phrase = models.CharField(max_length=255)
    source_pattern = models.CharField(max_length=255, blank=True, default="")
    target_pattern = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "seo_interlinkexclusion"
        verbose_name = "Interlink Exclusion"
        verbose_name_plural = "Interlink Exclusions"
        ordering: ClassVar[list[str]] = ["phrase"]
        unique_together = ("phrase", "source_pattern", "target_pattern")

    def __str__(self) -> str:
        return self.phrase


class CrawlRun(TimestampedModel):
    """One bounded crawl of the deployed site, fetched through ``SafeFetcher``."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Trigger(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    base_url = models.URLField(max_length=500)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING
    )
    trigger = models.CharField(
        max_length=16, choices=Trigger.choices, default=Trigger.MANUAL
    )
    max_pages = models.PositiveIntegerField(default=300)
    pages_crawled = models.PositiveIntegerField(default=0)
    pages_with_errors = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    robots_status = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seo_crawl_runs",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Crawl {self.pk} ({self.status})"

    @property
    def health_percent(self) -> int | None:
        """Share of crawled pages without an error-level issue; ``None`` if empty."""
        if not self.pages_crawled:
            return None
        clean = self.pages_crawled - self.pages_with_errors
        return round(100 * clean / self.pages_crawled)


class PageObservation(models.Model):
    """What a crawler actually received for one URL (raw HTTP response only)."""

    run = models.ForeignKey(
        CrawlRun, on_delete=models.CASCADE, related_name="observations"
    )
    url = models.CharField(max_length=2000)
    final_url = models.CharField(max_length=2000, blank=True, default="")
    status_code = models.PositiveIntegerField(null=True, blank=True)
    fetch_error = models.CharField(max_length=500, blank=True, default="")
    redirect_chain = models.JSONField(default=list, blank=True)
    content_type = models.CharField(max_length=100, blank=True, default="")
    response_ms = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    depth = models.PositiveSmallIntegerField(default=0)
    blocked_by_robots = models.BooleanField(default=False)
    in_sitemap = models.BooleanField(default=False)
    titles = models.JSONField(default=list, blank=True)
    descriptions = models.JSONField(default=list, blank=True)
    canonicals = models.JSONField(default=list, blank=True)
    robots_meta = models.JSONField(default=list, blank=True)
    x_robots_tag = models.CharField(max_length=255, blank=True, default="")
    h1s = models.JSONField(default=list, blank=True)
    lang = models.CharField(max_length=20, blank=True, default="")
    hreflang = models.JSONField(default=list, blank=True)
    jsonld_types = models.JSONField(default=list, blank=True)
    jsonld_errors = models.PositiveSmallIntegerField(default=0)
    internal_links = models.JSONField(default=list, blank=True)
    images_missing_alt = models.PositiveIntegerField(default=0)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["depth", "url"]
        constraints = (
            models.UniqueConstraint(
                fields=["run", "url"], name="seo_observation_run_url_unique"
            ),
        )

    def __str__(self) -> str:
        return f"{self.status_code or 'ERR'} {self.url}"


class Issue(TimestampedModel):
    """A measured defect. Rows are upserted by fingerprint and closed when fixed."""

    class Severity(models.TextChoices):
        ERROR = "error", "Error"
        WARNING = "warning", "Warning"
        INFO = "info", "Info"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"

    code = models.CharField(max_length=64, db_index=True)
    severity = models.CharField(max_length=8, choices=Severity.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    url = models.CharField(max_length=2000, blank=True, default="")
    message = models.CharField(max_length=500)
    details = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    first_seen_run = models.ForeignKey(
        CrawlRun, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    last_seen_run = models.ForeignKey(
        CrawlRun, on_delete=models.SET_NULL, null=True, related_name="issues"
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["severity", "code", "url"]
        indexes = (
            models.Index(fields=["status", "severity"], name="seo_issue_open_idx"),
        )

    def __str__(self) -> str:
        return f"{self.code}: {self.url}"


class ChangeLedger(TimestampedModel):
    """Before/after record of every automated or manual SEO change, with rollback."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled back"

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"

    idempotency_key = models.CharField(max_length=128, unique=True)
    kind = models.CharField(max_length=64, db_index=True)
    action = models.CharField(max_length=8, choices=Action.choices)
    target_model = models.CharField(max_length=100)
    target_pk = models.CharField(max_length=64, blank=True, default="")
    target_label = models.CharField(max_length=255, blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=500)
    source = models.CharField(max_length=64, help_text="Rule, agent or user origin.")
    mode = models.CharField(max_length=20, blank=True, default="")
    requires_approval = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PROPOSED, db_index=True
    )
    validation = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seo_decisions",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        permissions: ClassVar[list[tuple[str, StrOrPromise]]] = [
            ("approve_changeledger", "Can approve or reject proposed SEO changes"),
            ("rollback_changeledger", "Can roll back applied SEO changes"),
            ("run_crawl", "Can start SEO crawls"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.target_label} ({self.status})"


class SEOJob(TimestampedModel):
    """Idempotent event-driven job. ``job_key`` collapses duplicate deliveries."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=64, db_index=True)
    job_key = models.CharField(max_length=190, unique=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=4)
    available_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes = (
            models.Index(fields=["status", "available_at"], name="seo_job_due_idx"),
        )

    def __str__(self) -> str:
        return f"{self.kind} {self.job_key} ({self.status})"
