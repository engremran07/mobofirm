"""
Enterprise-grade Tag Service Layer.
Handles all tag operations with AI suggestions, trending, analytics, and relationships.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Max
from django.utils import timezone
from django.utils.text import slugify

from apps.core import ai
from apps.core.events import event_bus

# ARCHIVED: Enhanced models moved to apps/core/versions/
# from apps.tags.models_enhanced import (
#     TagCategory, TagRelationship, TagTrending, TagAnalytics,
#     TagSubscription, TagSuggestion, TagBlacklist, TagMerge,
#     TagCollection, TagAlias
# )
from apps.core.infrastructure import EmailService, QueueService
from apps.core.metrics import metrics
from apps.tags.models import Tag

logger = logging.getLogger(__name__)


class TagService:
    """
    Centralized service for all tag operations.
    Provides enterprise-grade tagging with AI, analytics, and relationships.
    """

    def __init__(self):
        self.queue = QueueService()
        self.email = EmailService()

    # ==================== CRUD Operations ====================

    @transaction.atomic
    def create_tag(
        self,
        name: str,
        description: str = "",
        category: TagCategory | None = None,  # noqa: F821  # type: ignore[name-defined]
        parent: Tag | None = None,
        created_by: Any | None = None,
        is_curated: bool = False,
    ) -> Tag:
        """
        Create new tag with validation and setup.

        Args:
            name: Tag name
            description: Tag description
            category: Optional category
            parent: Optional parent tag for hierarchy
            created_by: User creating the tag
            is_curated: Whether tag is staff-curated

        Returns:
            Created Tag instance
        """
        metrics.increment("tag.create")

        # Normalize name
        normalized = name.strip().lower()

        # Check blacklist
        if self._is_blacklisted(normalized):
            raise ValueError(f"Tag name '{name}' is blacklisted")

        # Check for alias
        alias = TagAlias.objects.filter(alias=normalized).first()  # noqa: F821  # type: ignore[name-defined]
        if alias:
            logger.info(f"Tag name '{name}' is an alias for {alias.canonical_tag}")
            return alias.canonical_tag

        # Create tag
        tag = Tag.objects.create(
            name=name.strip(),
            normalized_name=normalized,
            slug=slugify(normalized),
            description=description,
            parent=parent,
            is_curated=is_curated,
        )

        # Create analytics
        TagAnalytics.objects.create(tag=tag, first_used=timezone.now())  # noqa: F821  # type: ignore[name-defined]

        # Publish event
        event_bus.publish(
            "tag.created",
            {
                "tag_id": tag.pk,
                "name": tag.name,
                "created_by": created_by.id if created_by else None,
            },
        )

        logger.info(f"Tag '{name}' created (ID: {tag.pk})")
        return tag

    @transaction.atomic
    def update_tag(
        self,
        tag: Tag,
        name: str | None = None,
        description: str | None = None,
        category: TagCategory | None = None,  # noqa: F821  # type: ignore[name-defined]
        is_curated: bool | None = None,
    ) -> Tag:
        """Update tag properties."""
        metrics.increment("tag.update")

        if name:
            tag.name = name.strip()
            tag.normalized_name = name.strip().lower()
            tag.slug = slugify(tag.normalized_name)

        if description is not None:
            tag.description = description

        if is_curated is not None:
            tag.is_curated = is_curated

        tag.save()

        logger.info(f"Tag {tag.pk} updated")
        return tag

    @transaction.atomic
    def merge_tags(
        self,
        source_tag: Tag,
        target_tag: Tag,
        merged_by: Any | None = None,
        reason: str = "",
    ) -> TagMerge:  # noqa: F821  # type: ignore[name-defined]
        """
        Merge source tag into target tag.
        Migrates all tagged items and creates redirect.
        """
        metrics.increment("tag.merge")

        # Get all items tagged with source
        from apps.tags.models_tagged_item import TaggedItem

        items = TaggedItem.objects.filter(tag=source_tag)
        items.count()

        # Migrate to target tag
        migrated = 0
        for item in items:
            # Check if target tag already exists for this item
            exists = TaggedItem.objects.filter(
                tag=target_tag, content_type=item.content_type, object_id=item.object_id
            ).exists()

            if not exists:
                item.tag = target_tag
                item.save()
                migrated += 1
            else:
                item.delete()  # Remove duplicate

        # Update usage counts
        target_tag.usage_count = F("usage_count") + migrated
        target_tag.save(update_fields=["usage_count"])

        # Mark source as merged
        source_tag.merge_into = target_tag
        source_tag.is_active = False
        source_tag.save(update_fields=["merge_into", "is_active"])

        # Create merge record
        merge = TagMerge.objects.create(  # noqa: F821  # type: ignore[name-defined]
            source_tag_name=source_tag.name,
            target_tag=target_tag,
            merged_by=merged_by,
            reason=reason,
            items_migrated=migrated,
        )

        logger.info(
            f"Merged tag '{source_tag.name}' into '{target_tag.name}' ({migrated} items)"
        )
        return merge

    # ==================== AI Suggestions ====================

    def suggest_tags_for_content(
        self,
        content: str,
        title: str = "",
        existing_tags: list[str] | None = None,
        max_suggestions: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Use AI to suggest relevant tags for content.

        Returns:
            List of dicts with 'name', 'confidence', 'rationale'
        """
        metrics.increment("tag.ai_suggest")

        # Check cache
        import hashlib

        cache_key = f"tag_suggestions:{hashlib.md5((content + title).encode()).hexdigest()[:16]}"  # noqa: S324
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Prepare prompt
        existing_str = ", ".join(existing_tags) if existing_tags else "none"
        prompt = f"""Suggest relevant tags for the following content.

Title: {title}

Content (first 1000 chars):
{content[:1000]}

Existing tags: {existing_str}

Suggest up to {max_suggestions} additional tags that:
1. Are specific and relevant
2. Help with discoverability
3. Follow common tagging conventions
4. Don't duplicate existing tags

For each tag, provide:
- name: The tag name
- confidence: Score 0-1
- rationale: Brief reason why this tag fits

Format as JSON array: [{{"name": "...", "confidence": 0.9, "rationale": "..."}}]
"""

        try:
            # Create a system user for AI calls (or use anonymous)
            from django.contrib.auth import get_user_model

            User = get_user_model()
            try:
                system_user = User.objects.filter(is_staff=True).first()
                if not system_user:
                    system_user = User.objects.first()
            except:  # noqa: E722
                # AI client not available
                return []

            if not system_user:
                return []

            # Use AI client for text generation
            response = ai.generate_text(prompt, constraints={"max_tokens": 500})

            # Parse response
            import json

            # Extract JSON from response (handles markdown code blocks)
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                suggestions = json.loads(json_match.group(0))
            else:
                suggestions = []

            # Validate and normalize
            validated = []
            for sug in suggestions:
                if isinstance(sug, dict) and "name" in sug:
                    validated.append(
                        {
                            "name": sug["name"],
                            "confidence": float(sug.get("confidence", 0.5)),
                            "rationale": sug.get("rationale", ""),
                        }
                    )

            # Cache results
            cache.set(cache_key, validated, timeout=3600)  # 1 hour

            logger.info(f"AI suggested {len(validated)} tags")
            return validated

        except Exception as e:
            logger.exception(f"AI tag suggestion failed: {e}")
            return []

    @transaction.atomic
    def create_tag_suggestion(
        self,
        suggested_name: str,
        suggested_by: Any,
        description: str = "",
        ai_generated: bool = False,
        ai_confidence: float = 0.0,
    ) -> TagSuggestion:  # noqa: F821  # type: ignore[name-defined]
        """
        Create tag suggestion for review.
        """
        normalized = suggested_name.strip().lower()

        # Check if tag already exists
        existing = Tag.objects.filter(normalized_name=normalized).first()
        if existing:
            raise ValueError(f"Tag '{suggested_name}' already exists")

        suggestion = TagSuggestion.objects.create(  # noqa: F821  # type: ignore[name-defined]
            suggested_name=suggested_name.strip(),
            normalized_name=normalized,
            description=description,
            suggested_by=suggested_by,
            ai_generated=ai_generated,
            ai_confidence=ai_confidence,
            status=TagSuggestion.Status.PENDING,  # noqa: F821  # type: ignore[name-defined]
        )

        logger.info(f"Tag suggestion created: '{suggested_name}' by {suggested_by}")
        return suggestion

    @transaction.atomic
    def approve_tag_suggestion(
        self,
        suggestion: TagSuggestion,  # noqa: F821  # type: ignore[name-defined]
        reviewed_by: Any,
    ) -> Tag:
        """
        Approve tag suggestion and create tag.
        """
        tag = self.create_tag(
            name=suggestion.suggested_name,
            description=suggestion.description,
            created_by=suggestion.suggested_by,
            is_curated=True,
        )

        suggestion.status = TagSuggestion.Status.APPROVED  # noqa: F821  # type: ignore[name-defined]
        suggestion.reviewed_by = reviewed_by
        suggestion.reviewed_at = timezone.now()
        suggestion.created_tag = tag
        suggestion.save()

        # Notify suggester
        self.queue.enqueue(
            "apps.tags.tasks.notify_suggestion_approved", suggestion_id=suggestion.id
        )

        logger.info(f"Tag suggestion approved: '{suggestion.suggested_name}'")
        return tag

    # ==================== Relationships ====================

    @transaction.atomic
    def create_relationship(
        self,
        from_tag: Tag,
        to_tag: Tag,
        relationship_type: str,
        strength: float = 1.0,
        created_by: Any | None = None,
    ) -> TagRelationship:  # noqa: F821  # type: ignore[name-defined]
        """
        Create relationship between tags.
        """
        relationship, created = TagRelationship.objects.get_or_create(  # noqa: F821  # type: ignore[name-defined]
            from_tag=from_tag,
            to_tag=to_tag,
            relationship_type=relationship_type,
            defaults={"strength": strength, "created_by": created_by},
        )

        if not created:
            relationship.strength = strength
            relationship.save(update_fields=["strength"])

        logger.info(
            f"Created relationship: {from_tag} → {relationship_type} → {to_tag}"
        )
        return relationship

    def get_related_tags(
        self,
        tag: Tag,
        relationship_type: str | None = None,
        min_strength: float = 0.5,
        limit: int = 10,
    ) -> list[Tag]:
        """
        Get tags related to given tag.
        """
        qs = TagRelationship.objects.filter(  # noqa: F821  # type: ignore[name-defined]
            from_tag=tag, strength__gte=min_strength
        ).select_related("to_tag")

        if relationship_type:
            qs = qs.filter(relationship_type=relationship_type)

        qs = qs.order_by("-strength", "-to_tag__usage_count")[:limit]

        return [rel.to_tag for rel in qs]

    def discover_related_tags(
        self, tag: Tag, min_co_occurrence: int = 3
    ) -> list[tuple[Tag, int]]:
        """
        Discover related tags based on co-occurrence.
        Returns list of (tag, co_occurrence_count) tuples.
        """
        from apps.tags.models_tagged_item import TaggedItem

        # Get all items tagged with this tag
        tagged_items = TaggedItem.objects.filter(tag=tag).values(
            "content_type_id", "object_id"
        )

        # Find tags that appear on same items
        related = (
            TaggedItem.objects.filter(
                content_type_id__in=[item["content_type_id"] for item in tagged_items],
                object_id__in=[item["object_id"] for item in tagged_items],
            )
            .exclude(tag=tag)
            .values("tag")
            .annotate(count=Count("id"))
            .filter(count__gte=min_co_occurrence)
            .order_by("-count")[:20]
        )

        # Get tag objects
        tag_ids = [r["tag"] for r in related]
        tags = {t.pk: t for t in Tag.objects.filter(id__in=tag_ids)}

        result = [(tags[r["tag"]], r["count"]) for r in related if r["tag"] in tags]

        return result

    # ==================== Trending ====================

    @transaction.atomic
    def update_trending_tags(self, period: str = "daily"):
        """
        Calculate and update trending tags for period.
        Run as periodic task.
        """
        metrics.increment(f"tag.trending.update.{period}")

        # Define time windows
        period_map = {
            "hourly": timedelta(hours=1),
            "daily": timedelta(days=1),
            "weekly": timedelta(days=7),
            "monthly": timedelta(days=30),
        }

        if period not in period_map:
            raise ValueError(f"Invalid period: {period}")

        now = timezone.now()
        start_time = now - period_map[period]

        # Get tag usage in period
        from apps.tags.models_tagged_item import TaggedItem

        usage = (
            TaggedItem.objects.filter(created_at__gte=start_time)
            .values("tag")
            .annotate(count=Count("id"))
            .order_by("-count")[:100]
        )

        # Calculate previous period for growth rate
        prev_start = start_time - period_map[period]
        prev_usage = {
            item["tag"]: item["count"]
            for item in TaggedItem.objects.filter(
                created_at__gte=prev_start, created_at__lt=start_time
            )
            .values("tag")
            .annotate(count=Count("id"))
        }

        # Update trending records
        for rank, item in enumerate(usage, start=1):
            tag_id = item["tag"]
            current_count = item["count"]
            prev_count = prev_usage.get(tag_id, 0)

            # Calculate growth rate
            if prev_count > 0:
                growth_rate = ((current_count - prev_count) / prev_count) * 100
            else:
                growth_rate = 100.0 if current_count > 0 else 0.0

            # Calculate trending score (combines usage + growth)
            trending_score = current_count * (1 + growth_rate / 100)

            # Update or create
            TagTrending.objects.update_or_create(  # noqa: F821  # type: ignore[name-defined]
                tag_id=tag_id,
                period=period,
                defaults={
                    "usage_count": current_count,
                    "growth_rate": growth_rate,
                    "trending_score": trending_score,
                    "rank": rank,
                    "calculated_at": now,
                },
            )

        logger.info(f"Updated {len(usage)} trending tags for period '{period}'")

    def get_trending_tags(self, period: str = "daily", limit: int = 10) -> list[Tag]:
        """
        Get trending tags for period.
        """
        cache_key = f"trending_tags:{period}:{limit}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        trending = (
            TagTrending.objects.filter(period=period)  # noqa: F821  # type: ignore[name-defined]
            .select_related("tag")
            .order_by("rank")[:limit]
        )

        tags = [t.tag for t in trending]

        cache.set(cache_key, tags, timeout=300)  # 5 minutes
        return tags

    # ==================== Analytics ====================

    @transaction.atomic
    def update_tag_analytics(self, tag_id: int):
        """
        Update comprehensive analytics for tag.
        Run periodically for all tags.
        """
        try:
            tag = Tag.objects.get(id=tag_id)
        except Tag.DoesNotExist:
            return

        analytics, _ = TagAnalytics.objects.get_or_create(tag=tag)  # noqa: F821  # type: ignore[name-defined]

        # Get usage data
        from apps.tags.models_tagged_item import TaggedItem

        items = TaggedItem.objects.filter(tag=tag)

        analytics.total_usage = items.count()
        analytics.last_used = items.aggregate(Max("created_at"))["created_at__max"]

        # Update tag usage_count
        tag.usage_count = analytics.total_usage
        tag.save(update_fields=["usage_count"])

        # Calculate growth rates
        now = timezone.now()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        usage_7d = items.filter(created_at__gte=week_ago).count()
        usage_30d = items.filter(created_at__gte=month_ago).count()
        usage_prev_7d = items.filter(
            created_at__gte=week_ago - timedelta(days=7), created_at__lt=week_ago
        ).count()
        usage_prev_30d = items.filter(
            created_at__gte=month_ago - timedelta(days=30), created_at__lt=month_ago
        ).count()

        # Calculate growth rates
        if usage_prev_7d > 0:
            analytics.growth_rate_7d = (
                (usage_7d - usage_prev_7d) / usage_prev_7d
            ) * 100
        else:
            analytics.growth_rate_7d = 0.0

        if usage_prev_30d > 0:
            analytics.growth_rate_30d = (
                (usage_30d - usage_prev_30d) / usage_prev_30d
            ) * 100
        else:
            analytics.growth_rate_30d = 0.0

        analytics.save()

        logger.debug(f"Updated analytics for tag {tag_id}")

    def get_tag_stats(self, tag: Tag) -> dict[str, Any]:
        """
        Get comprehensive statistics for tag.
        """
        try:
            analytics = tag.analytics  # type: ignore[attr-defined]
        except TagAnalytics.DoesNotExist:  # noqa: F821  # type: ignore[name-defined]
            analytics = TagAnalytics.objects.create(tag=tag)  # noqa: F821  # type: ignore[name-defined]

        from apps.tags.models_tagged_item import TaggedItem

        # Recent usage
        now = timezone.now()
        usage_24h = TaggedItem.objects.filter(
            tag=tag, created_at__gte=now - timedelta(hours=24)
        ).count()
        usage_7d = TaggedItem.objects.filter(
            tag=tag, created_at__gte=now - timedelta(days=7)
        ).count()

        # Related tags
        related = self.get_related_tags(tag, limit=5)

        # Trending data
        trending = TagTrending.objects.filter(tag=tag).values(  # noqa: F821  # type: ignore[name-defined]
            "period", "rank", "trending_score"
        )

        return {
            "total_usage": analytics.total_usage,
            "usage_24h": usage_24h,
            "usage_7d": usage_7d,
            "growth_rate_7d": analytics.growth_rate_7d,
            "growth_rate_30d": analytics.growth_rate_30d,
            "quality_score": analytics.quality_score,
            "first_used": analytics.first_used,
            "last_used": analytics.last_used,
            "related_tags": [{"name": t.name, "slug": t.slug} for t in related],
            "trending": list(trending),
        }

    # ==================== Subscriptions ====================

    @transaction.atomic
    def subscribe_to_tag(
        self, tag: Tag, user: Any, notification_frequency: str = "instant"
    ) -> TagSubscription:  # noqa: F821  # type: ignore[name-defined]
        """
        Subscribe user to tag notifications.
        """
        subscription, _created = TagSubscription.objects.update_or_create(  # noqa: F821  # type: ignore[name-defined]
            tag=tag,
            user=user,
            defaults={
                "notification_frequency": notification_frequency,
                "is_active": True,
            },
        )

        logger.info(f"User {user.id} subscribed to tag '{tag.name}'")
        return subscription

    @transaction.atomic
    def unsubscribe_from_tag(self, tag: Tag, user: Any) -> bool:
        """
        Unsubscribe user from tag.
        """
        deleted_count, _ = TagSubscription.objects.filter(tag=tag, user=user).delete()  # noqa: F821  # type: ignore[name-defined]

        return deleted_count > 0

    # ==================== Utilities ====================

    def _is_blacklisted(self, normalized_name: str) -> bool:
        """Check if tag name is blacklisted."""
        blacklist = TagBlacklist.objects.filter(is_active=True)  # noqa: F821  # type: ignore[name-defined]

        for item in blacklist:
            if item.is_regex:
                if re.match(item.pattern, normalized_name):
                    return True
            else:
                if item.pattern == normalized_name:
                    return True

        return False

    def normalize_tag_name(self, name: str) -> str:
        """Normalize tag name for consistency."""
        # Remove extra whitespace
        normalized = " ".join(name.split())
        # Lowercase
        normalized = normalized.lower()
        # Remove special characters except hyphens and underscores
        normalized = re.sub(r"[^\w\s-]", "", normalized)
        # Limit length
        normalized = normalized[:64]
        return normalized

    def auto_tag_content(
        self,
        content_object: Any,
        content: str,
        title: str = "",
        min_confidence: float = 0.7,
        max_tags: int = 5,
    ) -> list[Tag]:
        """
        Automatically tag content using AI suggestions.
        """
        # Get AI suggestions
        suggestions = self.suggest_tags_for_content(
            content=content,
            title=title,
            max_suggestions=max_tags * 2,  # Get extras to filter
        )

        # Filter by confidence
        filtered = [s for s in suggestions if s["confidence"] >= min_confidence][
            :max_tags
        ]

        # Get or create tags
        tags = []
        for sug in filtered:
            tag, _created = Tag.objects.get_or_create(
                normalized_name=self.normalize_tag_name(sug["name"]),
                defaults={
                    "name": sug["name"],
                    "ai_suggested": True,
                    "ai_score": sug["confidence"],
                },
            )
            tags.append(tag)

        logger.info(f"Auto-tagged content with {len(tags)} tags")
        return tags
