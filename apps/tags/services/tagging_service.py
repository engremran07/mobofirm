"""
Tagging service providing generic tagging functionality for any Django model.
Completely decoupled from specific content types.
"""

from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from django.utils.text import slugify


class TaggingService:
    """
    Centralized service for tagging any Django model.

    This service provides a clean API for managing tags without creating
    dependencies between the tags app and content apps.

    Example:
        service = TaggingService()

        # Tag a post
        service.tag_object(post, "python", created_by=user)

        # Get all tags for a post
        tags = service.get_tags_for_object(post)

        # Find all posts with a tag
        posts = service.get_objects_for_tag("python", Post)
    """

    def __init__(self):
        """Initialize the service with required models"""
        from apps.tags.models import Tag
        from apps.tags.models_tagged_item import TaggedItem

        self.Tag = Tag
        self.TaggedItem = TaggedItem

    @transaction.atomic
    def tag_object(self, obj: Any, tag_name: str, created_by: Any | None = None):
        """
        Add a tag to any Django model instance.

        Args:
            obj: Any Django model instance to tag
            tag_name: Name of the tag (will be created if doesn't exist)
            created_by: Optional user who is applying the tag

        Returns:
            TaggedItem instance

        Example:
            >>> service.tag_object(my_post, "django", created_by=request.user)
        """
        # Get or create the tag
        tag, _ = self.Tag.objects.get_or_create(
            name=tag_name.strip(), defaults={"slug": self._slugify_tag(tag_name)}
        )

        # Get content type for the object
        content_type = ContentType.objects.get_for_model(obj)

        # Create or get the tagging relationship
        tagged_item, created = self.TaggedItem.objects.get_or_create(
            tag=tag,
            content_type=content_type,
            object_id=obj.pk,
            defaults={"created_by": created_by},
        )

        # Update the tag's usage_count if a new TaggedItem was created
        if created:
            from django.db.models import F

            self.Tag.objects.filter(pk=tag.pk).update(usage_count=F("usage_count") + 1)
            tag.refresh_from_db()

        return tagged_item

    def untag_object(self, obj: Any, tag_name: str) -> bool:
        """
        Remove a specific tag from an object.

        Args:
            obj: The Django model instance
            tag_name: Name of the tag to remove

        Returns:
            True if tag was removed, False if tag didn't exist

        Example:
            >>> service.untag_object(my_post, "obsolete-tag")
        """
        try:
            tag = self.Tag.objects.get(name=tag_name.strip())
            content_type = ContentType.objects.get_for_model(obj)

            deleted_count, _ = self.TaggedItem.objects.filter(
                tag=tag, content_type=content_type, object_id=obj.pk
            ).delete()

            # Update the tag's usage_count if items were deleted
            if deleted_count > 0:
                from django.db.models import F
                from django.db.models.functions import Greatest

                self.Tag.objects.filter(pk=tag.pk).update(
                    usage_count=Greatest(F("usage_count") - deleted_count, 0)
                )

            return deleted_count > 0
        except self.Tag.DoesNotExist:
            return False

    def clear_all_tags(self, obj: Any) -> int:
        """
        Remove all tags from an object.

        Args:
            obj: The Django model instance

        Returns:
            Number of tags removed
        """
        content_type = ContentType.objects.get_for_model(obj)
        deleted_count, _ = self.TaggedItem.objects.filter(
            content_type=content_type, object_id=obj.pk
        ).delete()

        return deleted_count

    def get_tags_for_object(self, obj: Any):
        """
        Get all tags associated with an object.

        Args:
            obj: The Django model instance

        Returns:
            QuerySet of Tag objects

        Example:
            >>> tags = service.get_tags_for_object(my_post)
            >>> print([tag.name for tag in tags])
            ['python', 'django', 'web']
        """
        content_type = ContentType.objects.get_for_model(obj)

        return self.Tag.objects.filter(
            tagged_items__content_type=content_type, tagged_items__object_id=obj.pk
        ).distinct()

    def get_objects_for_tag(
        self, tag_name: str, model_class: type | None = None
    ) -> list:
        """
        Get all objects tagged with a specific tag.

        Args:
            tag_name: Name of the tag
            model_class: Optional model class to filter by (e.g., Post)

        Returns:
            List of tagged objects

        Example:
            >>> # Get all posts tagged with "python"
            >>> posts = service.get_objects_for_tag("python", Post)

            >>> # Get all objects (any model) tagged with "featured"
            >>> objects = service.get_objects_for_tag("featured")
        """
        try:
            tag = self.Tag.objects.get(name=tag_name.strip())
        except self.Tag.DoesNotExist:
            return []

        tagged_items = self.TaggedItem.objects.filter(tag=tag).select_related(
            "content_type"
        )

        if model_class:
            content_type = ContentType.objects.get_for_model(model_class)
            tagged_items = tagged_items.filter(content_type=content_type)

        # Return the actual objects
        return [item.content_object for item in tagged_items if item.content_object]

    @transaction.atomic
    def bulk_tag_objects(
        self, objects: list[Any], tag_names: list[str], created_by: Any | None = None
    ) -> list:
        """
        Add multiple tags to multiple objects efficiently.

        Args:
            objects: List of Django model instances
            tag_names: List of tag names to apply
            created_by: Optional user applying the tags

        Returns:
            List of created TaggedItem instances

        Example:
            >>> posts = Post.objects.filter(author=user)
            >>> service.bulk_tag_objects(posts, ["python", "tutorial"], user)
        """
        tagged_items = []

        for obj in objects:
            for tag_name in tag_names:
                tagged_item = self.tag_object(obj, tag_name, created_by)
                tagged_items.append(tagged_item)

        return tagged_items

    def sync_tags_for_object(
        self, obj: Any, tag_names: list[str], created_by: Any | None = None
    ):
        """
        Synchronize tags for an object (add new, remove old).

        This is useful when you want to replace all tags with a new set.

        Args:
            obj: The Django model instance
            tag_names: List of tag names that should be on the object
            created_by: Optional user performing the sync

        Example:
            >>> # Replace all tags with new set
            >>> service.sync_tags_for_object(
            ...     post,
            ...     ["python", "django", "new-tag"],
            ...     request.user
            ... )
        """
        # Get current tags
        current_tags = set(self.get_tags_for_object(obj).values_list("name", flat=True))
        desired_tags = {tag.strip() for tag in tag_names}

        # Remove tags that shouldn't be there
        tags_to_remove = current_tags - desired_tags
        for tag_name in tags_to_remove:
            self.untag_object(obj, tag_name)

        # Add new tags
        tags_to_add = desired_tags - current_tags
        for tag_name in tags_to_add:
            self.tag_object(obj, tag_name, created_by)

    def get_popular_tags(self, limit: int = 10, model_class: type | None = None):
        """
        Get the most frequently used tags.

        Args:
            limit: Maximum number of tags to return
            model_class: Optional model class to filter by

        Returns:
            QuerySet of Tag objects ordered by usage count

        Example:
            >>> # Get 10 most popular tags across all models
            >>> popular = service.get_popular_tags(10)

            >>> # Get 5 most popular tags for blog posts
            >>> popular_blog = service.get_popular_tags(5, Post)
        """
        queryset = self.Tag.objects.annotate(
            computed_usage_count=models.Count("tagged_items")
        )

        if model_class:
            content_type = ContentType.objects.get_for_model(model_class)
            queryset = queryset.filter(tagged_items__content_type=content_type)

        return queryset.order_by("-computed_usage_count")[:limit]

    def get_related_tags(self, tag_name: str, limit: int = 5):
        """
        Get tags that frequently appear together with the given tag.

        Args:
            tag_name: The reference tag name
            limit: Maximum number of related tags to return

        Returns:
            QuerySet of related Tag objects

        Example:
            >>> # Find tags often used with "python"
            >>> related = service.get_related_tags("python", 5)
        """
        try:
            tag = self.Tag.objects.get(name=tag_name.strip())
        except self.Tag.DoesNotExist:
            return self.Tag.objects.none()

        # Get objects tagged with this tag
        tagged_items = self.TaggedItem.objects.filter(tag=tag)

        # Get other tags on those same objects
        related_tag_ids = (
            self.TaggedItem.objects.filter(
                content_type__in=tagged_items.values("content_type"),
                object_id__in=tagged_items.values("object_id"),
            )
            .exclude(tag=tag)
            .values("tag")
            .annotate(count=models.Count("tag"))
            .order_by("-count")[:limit]
            .values_list("tag", flat=True)
        )

        return self.Tag.objects.filter(id__in=related_tag_ids)

    def search_tags(self, query: str, limit: int = 10):
        """
        Search for tags by name.

        Args:
            query: Search string
            limit: Maximum results

        Returns:
            QuerySet of matching Tag objects
        """
        return self.Tag.objects.filter(name__icontains=query.strip())[:limit]

    @staticmethod
    def _slugify_tag(tag_name: str) -> str:
        """Create URL-friendly slug from tag name"""
        return slugify(tag_name)[:120]

    def get_tag_cloud_data(
        self, model_class: type | None = None, min_count: int = 1
    ) -> list[dict]:
        """
        Get data for rendering a tag cloud.

        Args:
            model_class: Optional model to filter by
            min_count: Minimum usage count to include

        Returns:
            List of dicts with 'tag', 'count', and 'weight' keys

        Example:
            >>> cloud_data = service.get_tag_cloud_data(Post, min_count=2)
            >>> for item in cloud_data:
            ...     print(f"{item['tag']}: {item['weight']}")
        """
        # Build base queryset
        queryset = self.Tag.objects.all()

        # Apply model filter first, then annotate with distinct count
        if model_class:
            content_type = ContentType.objects.get_for_model(model_class)
            queryset = queryset.filter(
                tagged_items__content_type=content_type
            ).annotate(computed_usage_count=models.Count("tagged_items", distinct=True))
        else:
            queryset = queryset.annotate(
                computed_usage_count=models.Count("tagged_items", distinct=True)
            )

        # Filter by min_count and get top 100
        tags_data = list(
            queryset.filter(computed_usage_count__gte=min_count)
            .order_by("-computed_usage_count")[:100]
            .values("name", "slug", "computed_usage_count")
        )

        if not tags_data:
            return []

        # Calculate weights (1-5 scale for font sizing)
        counts = [item["computed_usage_count"] for item in tags_data]
        min_cnt = min(counts)
        max_cnt = max(counts)

        for item in tags_data:
            if max_cnt == min_cnt:
                weight = 3
            else:
                weight = 1 + int(
                    4 * (item["computed_usage_count"] - min_cnt) / (max_cnt - min_cnt)
                )
            item["weight"] = weight
            item["tag"] = item["name"]
            item["count"] = item["computed_usage_count"]

        return tags_data
