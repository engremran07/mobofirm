from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import F, Q


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    avatar = models.ImageField(upload_to="profiles/avatars/", null=True, blank=True)
    cover = models.ImageField(upload_to="profiles/covers/", null=True, blank=True)
    bio = models.TextField(max_length=1000, blank=True, default="")
    website = models.URLField(blank=True, default="")
    location = models.CharField(max_length=100, blank=True, default="")
    social_links = models.JSONField(default=dict, blank=True)
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"

    def __str__(self) -> str:
        return f"Profile({self.user})"


class Reputation(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reputation"
    )
    total_score = models.IntegerField(default=0, db_index=True)
    fw_contributions = models.PositiveIntegerField(default=0)
    downloads_helped = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Reputation"
        verbose_name_plural = "Reputations"
        ordering = ["-total_score"]

    def __str__(self) -> str:
        return f"{self.user} rep={self.total_score}"


class Following(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="following"
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="followers"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Following"
        verbose_name_plural = "Followings"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"], name="users_following_unique"
            ),
            models.CheckConstraint(
                condition=~Q(follower=F("following")), name="users_no_self_follow"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.follower} -> {self.following}"


class ActivityFeed(models.Model):
    class ActionType(models.TextChoices):
        UPLOAD = "upload", "Uploaded Firmware"
        DOWNLOAD = "download", "Downloaded Firmware"
        COMMENT = "comment", "Commented"
        REVIEW = "review", "Wrote Review"
        FOLLOW = "follow", "Followed User"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="activity_feed"
    )
    action_type = models.CharField(max_length=12, choices=ActionType.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL
    )
    object_id = models.BigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Activity"
        verbose_name_plural = "Activity Feed"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "action_type"], name="users_activity_action_idx"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.action_type} @ {self.created_at}"
