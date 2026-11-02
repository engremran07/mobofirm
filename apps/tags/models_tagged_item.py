"""
Generic tagging model using Django's ContentTypes framework.
Allows any model to be tagged without creating hard dependencies.
"""

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class TaggedItem(models.Model):
    """
    Junction table for generic tagging.

    This model creates a many-to-many relationship between Tag and any Django model
    without hardcoding foreign keys. This makes the tags app completely pluggable.

    Usage:
        # Tag any object
        from apps.tags.services.tagging_service import TaggingService
        service = TaggingService()
        service.tag_object(my_post, "python")

        # Get tags for an object
        tags = service.get_tags_for_object(my_post)
    """

    tag = models.ForeignKey(
        "Tag",
        on_delete=models.CASCADE,
        related_name="tagged_items",
        help_text="The tag being applied",
    )

    # Generic foreign key components
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, help_text="The model class being tagged"
    )
    object_id = models.PositiveIntegerField(
        db_index=True, help_text="The ID of the specific object being tagged"
    )
    content_object = GenericForeignKey("content_type", "object_id")

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tags_created",
        help_text="User who applied this tag",
    )

    class Meta:
        verbose_name = "Tagged Item"
        verbose_name_plural = "Tagged Items"
        unique_together = [["tag", "content_type", "object_id"]]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["tag", "content_type"]),
            models.Index(fields=["created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tag.name} → {self.content_object}"

    @property
    def target_model_name(self):
        """Human-readable name of the tagged model"""
        return self.content_type.model_class()._meta.verbose_name
