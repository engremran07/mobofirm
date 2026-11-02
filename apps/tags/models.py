from __future__ import annotations

from django.db import models
from django.utils.text import slugify
from solo.models import SingletonModel

from apps.core.models import SoftDeleteModel, TimestampedModel

# Legacy enhanced models removed - archived in apps/core/versions/
# Keeping import placeholder for historical migration compatibility
# from apps.tags.models_enhanced import (
#     TagCategory, TagRelationship, TagTrending, TagAnalytics,
#     TagSubscription, TagSuggestion, TagBlacklist, TagMerge,
#     TagCollection, TagCollectionItem, TagAlias
# )


class Tag(TimestampedModel, SoftDeleteModel):
    name = models.CharField(
        max_length=64, unique=True, help_text="Use concise, reusable names."
    )
    normalized_name = models.CharField(max_length=64, blank=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    description = models.TextField(
        blank=True, default="", help_text="Explain when to use this tag."
    )
    synonyms = models.JSONField(
        default=list,
        blank=True,
        help_text="Comma-separated alternatives users might search.",
    )
    usage_count = models.PositiveIntegerField(default=0)
    co_occurrence = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    ai_suggested = models.BooleanField(
        default=False, help_text="True if suggested by AI and not yet curated."
    )
    is_curated = models.BooleanField(
        default=False, help_text="True if reviewed/approved by staff."
    )
    merge_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_from",
        help_text="Soft-merge: redirects this tag to another canonical tag.",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        help_text="Optional parent for hierarchical taxonomies.",
    )
    path_cache = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Cached materialized path for fast tree queries.",
    )
    ai_score = models.FloatField(
        default=0.0, help_text="Confidence when AI suggested this tag."
    )
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Last content hash used for AI suggestions.",
    )
    last_suggested_at = models.DateTimeField(null=True, blank=True)
    suggestions = models.JSONField(
        default=list,
        blank=True,
        help_text="Recent AI suggestions with scores/rationales.",
    )
    importance = models.IntegerField(
        default=0, help_text="Boost for curated/priority tags."
    )
    synonyms_text = models.TextField(
        blank=True, default="", help_text="Editable comma-separated synonyms."
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        indexes = [
            models.Index(fields=["normalized_name"], name="tag_normalized_idx"),
            models.Index(fields=["slug"], name="tag_slug_idx"),
            models.Index(
                fields=["is_active", "importance", "-usage_count"],
                name="tag_active_importance_idx",
            ),
            models.Index(fields=["merge_into"], name="tag_merge_into_idx"),
            models.Index(fields=["parent"], name="tag_parent_idx"),
            models.Index(fields=["path_cache"], name="tag_path_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_name"],
                name="tag_unique_normalized",
                condition=models.Q(merge_into__isnull=True),
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        # Normalize name/slug for consistent lookups
        norm = (self.name or "").strip()
        self.name = norm
        self.normalized_name = (self.normalized_name or norm.lower())[:64]
        if not self.slug:
            self.slug = slugify(self.normalized_name)[:80]
        super().save(*args, **kwargs)


class TagsSettings(SingletonModel):
    """
    Per-app settings for the Tags module so it can be reused independently.
    """

    allow_public_suggestions = models.BooleanField(
        default=True, help_text="Allow authenticated users to suggest new tags."
    )
    enable_ai_suggestions = models.BooleanField(
        default=True, help_text="Enable AI-assisted tag suggestions where available."
    )
    show_tag_usage = models.BooleanField(
        default=True, help_text="Expose tag usage counts in public views."
    )

    class Meta:
        verbose_name = "Tags Settings"

    def __str__(self) -> str:
        return "Tags Settings"


# Import TaggedItem to register it with Django's model system
from apps.tags.models_tagged_item import TaggedItem  # noqa: E402

__all__ = ["Tag", "TaggedItem", "TagsSettings"]
