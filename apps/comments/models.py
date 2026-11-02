from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxLengthValidator, MinLengthValidator
from django.db import models
from django.utils import timezone
from solo.models import SingletonModel

from apps.core.models import SoftDeleteModel, TimestampedModel

# Legacy enhanced models removed - archived in apps/core/versions/
# Keeping import placeholder for historical migration compatibility
# from apps.comments.models_enhanced import (
#     CommentReaction, CommentVote, CommentFlag, CommentMention,
#     CommentEdit, CommentBookmark, CommentAward, CommentAnalytics,
#     CommentThread, ModerationAction
# )


class Comment(TimestampedModel, SoftDeleteModel):
    """
    Represents a user comment on any content object in the system.

    Comment is a generic content model supporting comments on posts, pages, products,
    or any Django model via GenericForeignKey. Supports threading (parent-child),
    moderation workflows, AI toxicity detection, and metadata enrichment.

    Features:
    - Generic target support (post, blog page, product, custom model)
    - Threading/nesting with parent-child relationships
    - Moderation workflow (pending → approved/rejected/spam)
    - AI toxicity scoring (0.0 = safe, 1.0 = harmful)
    - Edit tracking (edited_at timestamp)
    - Reputation scoring (user voting)
    - Soft delete for audit trail
    - Full-text search support via metadata

    Attributes:
        content_type (ForeignKey): Django ContentType for generic FK target.
            Allows comments on any model (Post, Page, Product, etc.).
        object_id (PositiveIntegerField): Primary key of the target object.
            Together with content_type, identifies what's being commented on.
        content_object (GenericForeignKey): Dynamic reference to the commented object.
        post (ForeignKey): Legacy field for backward compatibility (deprecated).
            New code should use content_type + object_id.
        user (ForeignKey): User who wrote this comment.
        body (TextField): Comment text (3-5000 characters). Sanitized HTML or
            plain text from rich editor. Must be at least 3 chars, max 5000.
        parent (ForeignKey): Parent comment for threading. None = top-level comment.
            Can reference self for tree structures. Related name: children.
        status (CharField): Moderation status - pending (new), approved (visible),
            rejected (user deleted or mod rejected), spam (mod marked spam).
            PENDING is default, visible only to author and mods.
        is_approved (BooleanField): Auto-set to True when status=APPROVED.
            Denormalized for query efficiency.
        score (IntegerField): Reputation score from community voting (upvotes - downvotes).
            Used for ranking (hidden if negative).
        metadata (JSONField): Flexible storage for comment-specific data:
            {"word_count": 150, "mentions": [user_ids], "links": [urls], ...}
        moderation_flags (JSONField): Moderation system flags:
            {"spam_flagged": true, "report_count": 3, "mod_notes": "..."}
        toxicity_score (FloatField): AI moderation score (0.0-1.0).
            Computed by safety firewall. 0=safe, 1=highly toxic.
        edited_at (DateTimeField): When comment was last edited. null = never edited.
            Use to show "edited" indicator in UI.
        created_at (DateTimeField): When comment was created (from TimestampedModel).

    Meta:
        ordering: Most recent first (-created_at)
        indexes: (content_type, object_id, status) for efficient filtering by target

    Examples:
        >>> # Comment on a blog post (legacy style)
        >>> post = Post.objects.get(pk=1)
        >>> comment = Comment.objects.create(
        ...     post=post,
        ...     user=user,
        ...     body="Great article! I especially liked the section on security.",
        ...     status=Comment.Status.PENDING
        ... )
        >>> comment.save()  # save() syncs generic target from post

        >>> # Comment on any model using generic FK (new style)
        >>> from django.contrib.contenttypes.models import ContentType
        >>> from apps.products.models import Product
        >>> product = Product.objects.get(pk=42)
        >>> comment = Comment.objects.create(
        ...     content_type=ContentType.objects.get_for_model(Product),
        ...     object_id=product.pk,
        ...     user=user,
        ...     body="Does this come in blue?"
        ... )

        >>> # Threaded comment (reply to another comment)
        >>> parent_comment = Comment.objects.get(pk=5)
        >>> reply = Comment.objects.create(
        ...     post=parent_comment.post,
        ...     user=another_user,
        ...     body="Great question! It comes in blue and green.",
        ...     parent=parent_comment
        ... )

        >>> # Moderate a comment
        >>> comment = Comment.objects.get(pk=10)
        >>> comment.status = Comment.Status.APPROVED
        >>> comment.save()  # is_approved auto-set in save()

        >>> # Check toxicity (from AI moderation)
        >>> if comment.toxicity_score > 0.7:
        ...     comment.status = Comment.Status.SPAM
        ...     comment.moderation_flags['ai_flagged'] = True
        ...     comment.save()

        >>> # Find all approved comments on a post (with children)
        >>> post = Post.objects.get(pk=1)
        >>> top_level = post.comments.filter(
        ...     parent__isnull=True,
        ...     status=Comment.Status.APPROVED
        ... ).prefetch_related('children')
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SPAM = "spam", "Spam"

    # Generic target to support comments on any model; keep post for backward compatibility.
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    content_object = GenericForeignKey("content_type", "object_id")
    post = models.ForeignKey(
        "blog.Post",
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    body = models.TextField(
        validators=[MinLengthValidator(3), MaxLengthValidator(5000)],
        help_text="Plain text or sanitized HTML from editor; capped to 5k chars.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    is_approved = models.BooleanField(
        default=False,
        help_text="Auto-set when moderation marks approved.",
    )
    score = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    moderation_flags = models.JSONField(default=dict, blank=True)
    toxicity_score = models.FloatField(default=0.0)
    edited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Comment"
        verbose_name_plural = "Comments"
        indexes = [
            models.Index(fields=["content_type", "object_id", "status"]),
            models.Index(
                fields=["user", "status"]
            ),  # User's comments filtered by status
            models.Index(fields=["post", "status"]),  # Post comments filtered by status
            models.Index(fields=["created_at"]),  # Recent comments
            models.Index(fields=["toxicity_score"]),  # Moderation queue by toxicity
        ]

    def __str__(self) -> str:
        target = self.post or self.content_object or "unknown"
        return f"Comment by {self.user} on {target}"

    def save(self, *args, **kwargs):
        """
        Keep generic target in sync with legacy post FK to support gradual migration.

        Automatically:
        1. Syncs content_type + object_id from post if post exists
        2. Sets is_approved=True when status=APPROVED

        This enables gradual migration from post FK to generic FK without
        breaking existing code that only uses post.

        Args:
            *args: Passed to super().save()
            **kwargs: Passed to super().save()

        Returns:
            None

        Example:
            >>> comment = Comment(post=post, user=user, body="...")
            >>> comment.save()
            >>> # content_type and object_id are now populated from post
            >>> assert comment.content_type == ContentType.objects.get_for_model(Post)
        """
        # Legacy post FK support: sync to generic target
        if self.post and not (self.content_type and self.object_id):
            try:
                self.content_type = ContentType.objects.get_for_model(self.post)
                self.object_id = self.post.pk
            except Exception:  # noqa: S110
                pass
        # Auto-sync is_approved when status changes
        if self.status == self.Status.APPROVED and not self.is_approved:
            self.is_approved = True
        super().save(*args, **kwargs)


class CommentSettings(SingletonModel):
    """
    Singleton configuration for the comments system.

    CommentSettings is a singleton model (only one instance ever exists) that stores
    global configuration for comment functionality. This allows the comments app to be
    reused independently in other projects with different moderation/comment policies.

    Access via: CommentSettings.objects.get_solo()
    Never try to create multiple instances.

    Attributes:
        enable_comments (BooleanField): Master switch to enable/disable all commenting.
            When False, no comments can be created or viewed. Useful for temporarily
            disabling comments during moderation crises or maintenance.
        allow_anonymous (BooleanField): Whether unauthenticated users can post comments.
            If False (default), only logged-in users can comment. Useful for reducing
            spam. When False, comment form requires login.
        enable_ai_moderation (BooleanField): Enable AI-powered toxicity detection.
            When True, each comment is scored by safety firewall for harmful content.
            Scores above threshold auto-flag for manual review or auto-hide.

    Examples:
        >>> # Get singleton settings
        >>> settings = CommentSettings.objects.get_solo()
        >>>
        >>> # Check if comments are globally enabled
        >>> if settings.enable_comments:
        ...     # Show comment form
        ...     render_comment_section()
        >>>
        >>> # Change global settings
        >>> settings.enable_comments = False
        >>> settings.allow_anonymous = True
        >>> settings.save()
        >>>
        >>> # In templates, check before rendering
        >>> {% if comment_settings.enable_comments %}
        ...   {% include "comments/form.html" %}
        ... {% else %}
        ...   <p>Comments are currently disabled.</p>
        ... {% endif %}
        >>>
        >>> # In views, check before creating
        >>> settings = CommentSettings.objects.get_solo()
        >>> if not settings.enable_comments:
        ...     return JsonResponse(
        ...         {"error": "Comments disabled"},
        ...         status=403
        ...     )
        >>> if not settings.allow_anonymous and not user.is_authenticated:
        ...     return JsonResponse(
        ...         {"error": "Must be logged in to comment"},
        ...         status=401
        ...     )
    """

    enable_comments = models.BooleanField(default=True)
    allow_anonymous = models.BooleanField(default=False)
    enable_ai_moderation = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Comment Settings"

    def __str__(self) -> str:
        return "Comment Settings"
