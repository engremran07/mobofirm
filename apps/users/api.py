from __future__ import annotations

import datetime
import logging

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from rest_framework import permissions, serializers, viewsets
from rest_framework.authentication import SessionAuthentication

from .api_authentication import ExpiringTokenAuthentication
from .models import Notification
from .profile_models import ActivityFeed, Following, Profile
from .request_user import current_user

logger = logging.getLogger(__name__)


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = (
            "id",
            "user",
            "avatar",
            "cover",
            "bio",
            "website",
            "location",
            "social_links",
            "is_public",
            "updated_at",
        )
        read_only_fields = ("id", "user", "updated_at")

    def validate(self, attrs):
        if (
            self.instance is None
            and Profile.objects.filter(user=self.context["request"].user).exists()
        ):
            raise serializers.ValidationError(
                "A profile already exists for the authenticated user."
            )
        return attrs


class ProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ProfileSerializer
    authentication_classes = (SessionAuthentication, ExpiringTokenAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        profiles = Profile.objects.select_related("user")
        if self.request.user.is_staff:
            return profiles
        return profiles.filter(user=current_user(self.request))

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class FollowingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Following
        fields = ("id", "follower", "following", "created_at")
        read_only_fields = ("id", "follower", "created_at")

    def validate_following(self, following):
        follower = self.context["request"].user
        if follower.pk == following.pk:
            raise serializers.ValidationError("You cannot follow yourself.")
        existing = Following.objects.filter(follower=follower, following=following)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("You already follow this user.")
        return following


class FollowingViewSet(viewsets.ModelViewSet):
    serializer_class = FollowingSerializer
    authentication_classes = (SessionAuthentication, ExpiringTokenAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        return Following.objects.filter(follower=current_user(self.request))

    def perform_create(self, serializer):
        serializer.save(follower=self.request.user)


class ActivityFeedSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityFeed
        fields = ("id", "action_type", "object_id", "metadata", "created_at")
        read_only_fields = fields


class ActivityFeedViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityFeedSerializer
    authentication_classes = (SessionAuthentication, ExpiringTokenAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        return ActivityFeed.objects.filter(user=current_user(self.request))


@login_required
def notifications_unread_json(request: HttpRequest) -> JsonResponse:
    """
    Lightweight JSON endpoint for unread notifications.
    Returns up to 20 most recent unread items for header dropdowns.
    """
    qs = Notification.objects.filter(
        recipient=current_user(request), is_read=False
    ).order_by("-created_at")[:20]
    items = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "priority": n.priority,
            "channel": n.channel,
            "url": getattr(n, "url", None),
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in qs
    ]
    return JsonResponse({"items": items})


def notification_stats(request: HttpRequest) -> JsonResponse:
    """
    Quick notification statistics for admin dashboard widget.
    """
    if not getattr(request.user, "is_staff", False):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    from apps.users.models import CustomUser, PushSubscription

    today_start = timezone.make_aware(
        datetime.datetime.combine(timezone.now().date(), datetime.time.min)
    )
    try:
        stats = {
            "unread": Notification.objects.filter(is_read=False).count(),
            "active_users": CustomUser.objects.filter(is_active=True).count(),
            "push_subscriptions": PushSubscription.objects.filter(
                is_active=True
            ).count(),
            "today_notifications": Notification.objects.filter(
                created_at__gte=today_start
            ).count(),
        }
    except DatabaseError:
        logger.exception("notification_stats query failed")
        return JsonResponse(
            {"error": "Statistics are temporarily unavailable."}, status=503
        )
    return JsonResponse(stats)
