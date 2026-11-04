from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST
from rest_framework import permissions, serializers, viewsets

from .models import Notification
from .profile_models import ActivityFeed, Following, Profile
from .services.rate_limit import allow_action


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = [
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
        ]
        read_only_fields = ["id", "user", "updated_at"]

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
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        profiles = Profile.objects.select_related("user")
        if self.request.user.is_staff:
            return profiles
        return profiles.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class FollowingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Following
        fields = ["id", "follower", "following", "created_at"]
        read_only_fields = ["id", "follower", "created_at"]

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
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Following.objects.filter(follower=self.request.user)

    def perform_create(self, serializer):
        serializer.save(follower=self.request.user)


class ActivityFeedSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityFeed
        fields = ["id", "action_type", "object_id", "metadata", "created_at"]
        read_only_fields = fields


class ActivityFeedViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityFeedSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ActivityFeed.objects.filter(user=self.request.user)


@login_required
def notifications_unread_json(request: HttpRequest) -> JsonResponse:
    """
    Lightweight JSON endpoint for unread notifications.
    Returns up to 20 most recent unread items for header dropdowns.
    """
    qs = Notification.objects.filter(recipient=request.user, is_read=False).order_by(
        "-created_at"
    )[:20]
    items = [
        {
            "id": n.id,  # type: ignore[attr-defined]
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


@require_POST
def password_reset_verify(request: HttpRequest) -> JsonResponse:
    """
    Verify a password-reset code (token) inline and return the reset URL.
    This allows signed-in users to paste the code without clicking the email link.
    """
    email = (request.POST.get("email") or "").strip().lower()
    code = (request.POST.get("code") or "").strip()

    if not email or not code:
        return JsonResponse(
            {"ok": False, "error": "Email and code are required."}, status=400
        )

    # Per-IP/email throttle to prevent enumeration/guessing.
    rl_key = f"pwreset_verify:{request.META.get('REMOTE_ADDR', 'unknown')}:{email}"
    # FINDING-0016: fail_open=False — password reset must block on Redis failure.
    if not allow_action(rl_key, max_attempts=5, window_seconds=300, fail_open=False):
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Try again later."}, status=429
        )

    user = get_user_model().objects.filter(email__iexact=email).first()
    if not user:
        # Generic response to avoid user enumeration
        return JsonResponse(
            {"ok": False, "error": "Invalid or expired code."}, status=400
        )

    if not default_token_generator.check_token(user, code):
        return JsonResponse(
            {"ok": False, "error": "Invalid or expired code."}, status=400
        )

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    reset_url = reverse("account_reset_password_from_key", args=[uidb64, code])
    return JsonResponse({"ok": True, "redirect": reset_url})


def notification_stats(request: HttpRequest) -> JsonResponse:
    """
    Quick notification statistics for admin dashboard widget.
    """
    if not getattr(request.user, "is_staff", False):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    try:
        from django.utils import timezone

        from apps.users.models import CustomUser, Notification, PushSubscription

        today = timezone.now().date()
        import datetime

        today_start = datetime.datetime.combine(today, datetime.time.min)
        today_start = timezone.make_aware(today_start)

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

        return JsonResponse(stats)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
