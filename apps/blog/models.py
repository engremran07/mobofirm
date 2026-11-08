from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.core.validators import (
    MaxLengthValidator,
    MaxValueValidator,
    MinLengthValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import QuerySet
from django.db.models.functions import Lower
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from solo.models import SingletonModel

from apps.core.utils.sanitize import is_same_origin_path, sanitize_html


class PostStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In Review"
    SCHEDULED = "scheduled", "Scheduled"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


class PostAccess(models.TextChoices):
    """Who may read the full body (see ``apps.blog.paywall``)."""

    FREE = "free", "Free"
    FEATURED = "featured", "Featured (packages or credits)"
    PAID = "paid", "Paid (credits)"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        help_text="Optional parent to build nested categories (e.g., AI > Safety).",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["name"]
        verbose_name = "Blog Category"
        verbose_name_plural = "Categories"

    def __str__(self) -> str:
        if self.parent:
            return f"{self.parent} / {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)


class PostQuerySet(models.QuerySet):
    def live(self) -> PostQuerySet:
        """Posts readers may see: published and not scheduled for the future."""
        return self.filter(status=PostStatus.PUBLISHED, publish_at__lte=timezone.now())


class Post(models.Model):
    objects = PostQuerySet.as_manager()

    # Set by the pre_save signal so the post_save hook can detect a first publication.
    was_published_before_save: bool = False

    title = models.CharField(
        max_length=200,
        validators=[MinLengthValidator(3), MaxLengthValidator(200)],
        help_text="3-200 characters.",
    )
    slug = models.SlugField(max_length=240, unique=True, blank=True)
    summary = models.TextField(
        blank=True,
        default="",
        validators=[MaxLengthValidator(1000)],
        help_text="Short abstract shown in lists; capped to ~1000 characters.",
    )
    seo_title = models.CharField(max_length=240, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")
    canonical_url = models.URLField(blank=True, default="")
    hero_image = models.URLField(blank=True, default="")
    body = models.TextField(
        validators=[MinLengthValidator(10), MaxLengthValidator(50000)],
        help_text="HTML produced by the WYSIWYG editor; validated and sanitized upstream.",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        db_index=True,  # HIGH: Heavily filtered in category_detail view
    )
    firmware_brand = models.ForeignKey(
        "firmwares.Brand",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_posts",
        help_text="Auto-linked brand for firmware-related blog posts.",
    )
    firmware_model = models.ForeignKey(
        "firmwares.Model",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blog_posts",
        help_text="Auto-linked model for firmware-related blog posts.",
    )
    tags = models.ManyToManyField("tags.Tag", blank=True, related_name="posts")
    status = models.CharField(
        max_length=20,
        choices=PostStatus.choices,
        default=PostStatus.DRAFT,
        db_index=True,
    )
    publish_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)
    featured = models.BooleanField(
        default=False,
        db_index=True,  # MEDIUM: Queried in featured_posts view
    )
    reading_time = models.PositiveIntegerField(default=0, help_text="Minutes")
    version = models.PositiveIntegerField(default=1)
    is_ai_generated = models.BooleanField(default=False)
    ai_run_id = models.CharField(max_length=100, blank=True, default="")
    ai_error = models.TextField(blank=True, default="")
    allow_comments = models.BooleanField(default=True)
    noindex = models.BooleanField(
        default=False,
        help_text="If true, exclude from indexing until manually cleared.",
    )
    # Analytics fields
    views_count = models.PositiveIntegerField(default=0, help_text="Total views")
    likes_count = models.PositiveIntegerField(default=0, help_text="Total likes")
    comments_count = models.PositiveIntegerField(default=0, help_text="Total comments")

    # Rewarded ad gate — controls whether article body is locked behind an ad
    ad_gate_enabled = models.BooleanField(
        default=False,
        help_text="Manual toggle: require ad view before reading full article.",
    )
    ad_gate_min_views = models.PositiveIntegerField(
        default=0,
        help_text="Auto-enable ad gate when views_count >= this value. 0 = disabled.",
    )
    ad_gate_config = models.ForeignKey(
        "ads.RewardedAdConfig",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gated_posts",
        help_text="Rewarded ad configuration for this post's gate.",
    )

    access = models.CharField(
        max_length=10,
        choices=PostAccess.choices,
        default=PostAccess.FREE,
        db_index=True,
        help_text="Featured and paid posts show a teaser to readers without access.",
    )
    price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Staff-approved price in USD; converted to credits at the wallet rate.",
    )
    proposed_price = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="The author's suggested USD price; staff approve it on publication.",
    )
    price_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    price_approved_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The staff member who published or scheduled this post.",
    )
    guide_request = models.ForeignKey(
        "blog.GuideRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="solutions",
        help_text="The approved guide request this post answers.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-published_at", "-created_at"]
        verbose_name = "Blog Post"
        verbose_name_plural = "Blog Posts"
        indexes = (
            models.Index(
                fields=["status", "publish_at"], name="blog_post_status_pub_idx"
            ),
            models.Index(
                fields=["author", "status"], name="blog_post_author_status_idx"
            ),
            models.Index(Lower("slug"), name="blog_post_slug_ci_idx"),
        )

    @classmethod
    def published(cls) -> QuerySet:
        """Convenience queryset for live posts (published and not in the future)."""
        return cls.objects.live()

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("blog:post_detail", kwargs={"slug": self.slug})

    @property
    def is_live(self) -> bool:
        if self.status != PostStatus.PUBLISHED:
            return False
        return self.publish_at is None or self.publish_at <= timezone.now()

    @property
    def is_gated(self) -> bool:
        """Featured and paid posts withhold the body from readers without access."""
        return self.access != PostAccess.FREE

    @property
    def cover_image_url(self) -> str:
        """The hero image when it is served from this site (``img-src 'self'``)."""
        return self.hero_image if is_same_origin_path(self.hero_image) else ""

    def save(self, *args, **kwargs):
        self.body = sanitize_html(self.body)
        if self.access == PostAccess.FREE:
            self.price = self.proposed_price = None
            self.price_approved_by = None
            self.price_approved_at = None
        if not self.slug:
            base = slugify(self.title)[:230]
            candidate = base
            idx = 1
            while Post.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{idx}"
                idx += 1
            self.slug = candidate
        # Normalize status + published timestamps
        if self.status == PostStatus.PUBLISHED:
            self.is_published = True
            if not self.published_at:
                self.published_at = timezone.now()
            if not self.publish_at:
                self.publish_at = self.published_at
        elif self.status == PostStatus.SCHEDULED:
            self.is_published = False
            if self.publish_at and self.publish_at <= timezone.now():
                self.is_published = True
                self.published_at = self.publish_at
        else:
            self.is_published = False

        # Derive SEO title/description fallbacks
        if not self.seo_title:
            self.seo_title = self.title[:240]
        if not self.seo_description and self.summary:
            self.seo_description = self.summary[:320]

        # Estimate reading time (200 wpm)
        words = len(self.body.split())
        self.reading_time = max(1, round(words / 200)) if words else 1

        super().save(*args, **kwargs)

    @property
    def seo_ready(self) -> bool:
        return bool(self.seo_title and self.seo_description)


class PostLike(models.Model):
    """One like per user and post."""

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_likes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["post", "user"], name="blog_postlike_unique_user_post"
            ),
        )

    def __str__(self) -> str:
        return f"{self.user_id} likes {self.post_id}"


class GuideRequest(models.Model):
    """A member's request for a flashing method, pattern, trick or guide.

    Staff review every request first: it is private until approved, and only an
    approved request is public and can carry a credit bounty (``apps.bounty``).
    """

    class Kind(models.TextChoices):
        METHOD = "method", "Flashing method"
        PATTERN = "pattern", "Unlock pattern"
        TRICK = "trick", "Trick"
        GUIDE = "guide", "Guide"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"

    PUBLIC: ClassVar[tuple[str, ...]] = (Status.APPROVED, Status.FULFILLED)

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="guide_requests",
    )
    title = models.CharField(
        max_length=200,
        validators=[MinLengthValidator(8)],
        help_text="8-200 characters.",
    )
    description = models.TextField(
        validators=[MinLengthValidator(20), MaxLengthValidator(2000)],
        help_text="What you need, the device situation and what you already tried.",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GUIDE)
    brand = models.ForeignKey(
        "firmwares.Brand",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guide_requests",
    )
    firmware_model = models.ForeignKey(
        "firmwares.Model",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guide_requests",
    )
    chipset = models.ForeignKey(
        "firmwares.Chipset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guide_requests",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=500, blank=True)
    fulfilled_by_post = models.ForeignKey(
        "blog.Post",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        verbose_name = "Guide request"
        verbose_name_plural = "Guide requests"
        indexes = (
            models.Index(
                fields=["status", "-created_at"], name="blog_guidereq_status_idx"
            ),
        )

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("blog:request_detail", kwargs={"pk": self.pk})

    @property
    def is_public(self) -> bool:
        return self.status in self.PUBLIC

    @property
    def accepts_solutions(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def label(self) -> str:
        """Short device context for lists: brand, model and chipset when set."""
        parts = [
            self.brand.name if self.brand_id and self.brand else "",
            self.firmware_model.name
            if self.firmware_model_id and self.firmware_model
            else "",
            self.chipset.display_name if self.chipset_id and self.chipset else "",
        ]
        return " · ".join(part for part in parts if part)


class PostDraft(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="post_drafts"
    )
    post = models.ForeignKey(
        "blog.Post",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="drafts",
    )
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-updated_at"]

    def __str__(self) -> str:
        return f"Draft for {self.post or 'new post'} by {self.user}"


class PostRevision(models.Model):
    post = models.ForeignKey(
        "blog.Post", on_delete=models.CASCADE, related_name="revisions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="post_revisions",
    )
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"Revision {self.created_at} for {self.post}"


class BlogSettings(SingletonModel):
    """
    Per-app settings for the Blog module.
    """

    enable_blog = models.BooleanField(default=True)
    enable_blog_comments = models.BooleanField(default=True)
    allow_user_blog_posts = models.BooleanField(default=False)
    teaser_words = models.PositiveIntegerField(
        default=80,
        validators=[MinValueValidator(20), MaxValueValidator(300)],
        help_text="Words of a featured or paid article shown to readers without access.",
    )
    allow_guide_requests = models.BooleanField(
        default=True,
        help_text="Members may request a method or guide (staff still review each one).",
    )
    guide_requests_per_day = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="Most requests one member can send in 24 hours.",
    )
    guide_requests_open_limit = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="Most pending or approved requests one member may have at once.",
    )

    class Meta:
        verbose_name = "Blog Settings"

    def __str__(self) -> str:
        return "Blog Settings"


# -------------------------------------------------
# Multilingual (custom translation tables)
# -------------------------------------------------


class PostTranslation(models.Model):
    post = models.ForeignKey(
        "blog.Post", on_delete=models.CASCADE, related_name="translations"
    )
    language = models.CharField(max_length=10, db_index=True)
    title = models.CharField(max_length=200, blank=True, default="")
    summary = models.TextField(
        blank=True, default="", validators=[MaxLengthValidator(1000)]
    )
    body = models.TextField(blank=True, default="")
    seo_title = models.CharField(max_length=240, blank=True, default="")
    seo_description = models.CharField(max_length=320, blank=True, default="")

    class Meta:
        unique_together = ("post", "language")
        indexes = (models.Index(fields=["language"], name="post_translation_lang_idx"),)
        verbose_name = "Post Translation"
        verbose_name_plural = "Post Translations"

    def __str__(self) -> str:
        return f"{self.post} [{self.language}]"

    def save(self, *args, **kwargs):
        self.body = sanitize_html(self.body)
        super().save(*args, **kwargs)


class CategoryTranslation(models.Model):
    category = models.ForeignKey(
        "blog.Category", on_delete=models.CASCADE, related_name="translations"
    )
    language = models.CharField(max_length=10, db_index=True)
    name = models.CharField(max_length=120)

    class Meta:
        unique_together = ("category", "language")
        indexes = (
            models.Index(fields=["language"], name="category_translation_lang_idx"),
        )
        verbose_name = "Category Translation"
        verbose_name_plural = "Category Translations"

    def __str__(self) -> str:
        return f"{self.category} [{self.language}]"


class TagTranslation(models.Model):
    tag = models.ForeignKey(
        "tags.Tag", on_delete=models.CASCADE, related_name="translations"
    )
    language = models.CharField(max_length=10, db_index=True)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("tag", "language")
        indexes = (models.Index(fields=["language"], name="tag_translation_lang_idx"),)
        verbose_name = "Tag Translation"
        verbose_name_plural = "Tag Translations"

    def __str__(self) -> str:
        return f"{self.tag} [{self.language}]"


class AutoTopic(models.Model):
    """
    Queue for AI-generated blog posts.
    """

    STATUS_CHOICES: ClassVar[list[tuple[str, str]]] = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
    ]

    topic = models.CharField(max_length=240)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    ai_run_id = models.CharField(max_length=100, blank=True, default="")
    last_error = models.TextField(blank=True, default="")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    post = models.ForeignKey(
        "blog.Post", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes = (models.Index(fields=["status"], name="blog_autotopic_status_idx"),)

    def __str__(self) -> str:
        return f"{self.topic} ({self.status})"
