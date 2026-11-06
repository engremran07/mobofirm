from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from allauth.account.signals import user_signed_up
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.comments.views import _check_comment_rate_limit
from apps.core.signals import notification_send_requested, user_registered
from apps.users.models import Announcement, Notification, NotificationPreferences
from apps.users.profile_models import Following, Profile
from apps.users.services.notifications import send_notification
from apps.users.services.rate_limit import (
    allow_action,
    reset_rate_limit,
)


class RateLimitServiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allow_action_enforces_limits(self):
        key = "tests:rate_limit"
        for _ in range(5):
            assert allow_action(key, max_attempts=5, window_seconds=60)
        assert not allow_action(key, max_attempts=5, window_seconds=60)

    def test_reset_rate_limit_allows_new_attempts(self):
        key = "tests:rate_limit_reset"
        for _ in range(5):
            allow_action(key, max_attempts=5, window_seconds=60)
        assert not allow_action(key, max_attempts=5, window_seconds=60)
        reset_rate_limit(key)
        assert allow_action(key, max_attempts=5, window_seconds=60)


class CommentRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def _build_request(self, user_id: int | None = None):
        user = SimpleNamespace(pk=user_id or 0, is_authenticated=bool(user_id))
        return SimpleNamespace(
            user=user,
            META={"REMOTE_ADDR": "198.51.100.42"},
        )

    def test_comment_rate_limit_enforced(self):
        from apps.comments.views import PER_MINUTE

        request = self._build_request(user_id=42)
        for _ in range(PER_MINUTE):
            assert _check_comment_rate_limit(request)
        assert not _check_comment_rate_limit(request)


class UserProfileApiTests(TestCase):
    client: APIClient

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="profile_owner",
            email="profile-owner@example.com",
            password="Complex-test-password-123!",
        )
        self.other_user = user_model.objects.create_user(
            username="other_profile",
            email="other-profile@example.com",
            password="Complex-test-password-123!",
        )
        self.profile = Profile.objects.create(user=self.user, bio="Owner profile")
        self.other_profile = Profile.objects.create(
            user=self.other_user, bio="Private profile"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_profile_api_only_exposes_the_authenticated_users_profile(self):
        response = self.client.get(reverse("users:profiles-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.profile.pk)

    def test_profile_api_cannot_retrieve_another_users_profile(self):
        response = self.client.get(
            reverse("users:profiles-detail", args=[self.other_profile.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_profile_api_rejects_duplicate_profile_creation(self):
        response = self.client.post(
            reverse("users:profiles-list"), {"bio": "Duplicate profile"}
        )

        self.assertEqual(response.status_code, 400)

    def test_following_api_uses_authenticated_user_as_follower(self):
        response = self.client.post(
            reverse("users:following-list"),
            {"follower": self.other_user.pk, "following": self.other_user.pk},
        )

        self.assertEqual(response.status_code, 201)
        following = Following.objects.get(pk=response.data["id"])
        self.assertEqual(following.follower, self.user)
        self.assertEqual(following.following, self.other_user)

    def test_following_api_rejects_self_follow(self):
        response = self.client.post(
            reverse("users:following-list"), {"following": self.user.pk}
        )

        self.assertEqual(response.status_code, 400)

    def test_notification_signal_persists_a_valid_moderation_notification(self):
        notification_send_requested.send(
            sender=self.__class__,
            user_ids=[self.user.pk],
            title="Firmware rejected",
            message="The upload did not meet the requirements.",
            action_type="moderation",
            priority="warning",
        )

        notification = Notification.objects.get(recipient=self.user)
        self.assertEqual(notification.action_type, "moderation")
        self.assertEqual(notification.priority, "warning")
        self.assertEqual(notification.channel, "web")

    def test_notification_signal_rejects_unknown_action_types(self):
        with self.assertRaises(ValueError):
            notification_send_requested.send(
                sender=self.__class__,
                user_ids=[self.user.pk],
                title="Invalid",
                message="Invalid action",
                action_type="firmware_rejected",
            )

    def test_welcome_notification_accepts_signup_signals_and_is_idempotent(self):
        request = RequestFactory().get("/users/signup/")

        user_signed_up.send(sender=self.user.__class__, user=self.user, request=request)
        user_registered.send(
            sender=self.user.__class__, user=self.user, request=request
        )

        welcome_notifications = Notification.objects.filter(
            recipient=self.user, title="Welcome!", action_type="system"
        )
        self.assertEqual(welcome_notifications.count(), 1)
        notification = welcome_notifications.get()
        self.assertEqual(notification.priority, "info")
        self.assertEqual(notification.channel, "web")

    def test_send_notification_defaults_to_web_channel(self):
        notification = send_notification(
            recipient=self.user,
            title="Password changed",
            message="Your account password was changed.",
        )

        assert notification is not None
        self.assertEqual(notification.channel, "web")


class AnnouncementWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="announcement_viewer",
            email="announcement-viewer@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_unpublished_announcement_is_not_active(self):
        announcement = Announcement.objects.create(
            title="Scheduled notice",
            message="This notice is not published yet.",
            audience="all",
        )

        self.assertFalse(announcement.active_now())

    def test_dashboard_only_shows_published_current_announcements_for_audience(self):
        Announcement.objects.create(
            title="Live announcement",
            message="Visible to the account holder.",
            audience="all",
            is_active=True,
        )
        Announcement.objects.create(
            title="Draft announcement",
            message="Not published.",
            audience="all",
        )
        Announcement.objects.create(
            title="Staff announcement",
            message="Staff only.",
            audience="staff",
            is_active=True,
        )

        response = self.client.get(reverse("users:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Live announcement")
        self.assertNotContains(response, "Draft announcement")
        self.assertNotContains(response, "Staff announcement")

    def test_admin_can_publish_and_unpublish_announcement(self):
        admin_user = get_user_model().objects.create_user(
            username="announcement_admin",
            email="announcement-admin@example.com",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        announcement = Announcement.objects.create(
            title="Admin-managed announcement",
            message="Published through the admin action.",
            audience="all",
        )
        self.client.force_login(admin_user)
        changelist_url = reverse("admin:users_announcement_changelist")

        response = self.client.post(
            changelist_url,
            {
                "action": "publish_selected",
                "_selected_action": [announcement.pk],
                "index": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        announcement.refresh_from_db()
        self.assertTrue(announcement.active_now())

        response = self.client.post(
            changelist_url,
            {
                "action": "unpublish_selected",
                "_selected_action": [announcement.pk],
                "index": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        announcement.refresh_from_db()
        self.assertFalse(announcement.active_now())

    def test_published_announcement_respects_start_and_expiry(self):
        now = timezone.now()
        announcement = Announcement.objects.create(
            title="Live notice",
            message="This notice is live.",
            audience="all",
            is_active=True,
            start_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )

        self.assertFalse(announcement.active_now())

        announcement.start_at = now - timedelta(minutes=1)
        announcement.save(update_fields=["start_at"])

        self.assertTrue(announcement.active_now())

        announcement.expires_at = now - timedelta(minutes=1)
        announcement.save(update_fields=["expires_at"])

        self.assertFalse(announcement.active_now())


class NotificationReadBehaviorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="notification_reader",
            email="notification-reader@example.com",
            password="test-password",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other_notification_reader",
            email="other-notification-reader@example.com",
            password="test-password",
        )
        self.notification = Notification.objects.create(
            recipient=self.user,
            title="Unread notification",
            message="This notification must only be marked read by an explicit action.",
        )
        self.client.force_login(self.user)

    def test_get_displays_notification_without_marking_it_read(self):
        response = self.client.get(
            reverse("users:notification_detail", args=[self.notification.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mark as read")
        self.notification.refresh_from_db()
        self.assertFalse(self.notification.is_read)
        self.assertIsNone(self.notification.read_at)

    def test_post_marks_notification_read(self):
        response = self.client.post(
            reverse("users:notification_detail", args=[self.notification.pk])
        )

        self.assertRedirects(
            response,
            reverse("users:notification_detail", args=[self.notification.pk]),
        )
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)
        self.assertIsNotNone(self.notification.read_at)

    def test_user_cannot_read_or_mark_another_users_notification(self):
        other_notification = Notification.objects.create(
            recipient=self.other_user,
            title="Private notification",
            message="Private",
        )

        response = self.client.post(
            reverse("users:notification_detail", args=[other_notification.pk])
        )

        self.assertEqual(response.status_code, 404)
        other_notification.refresh_from_db()
        self.assertFalse(other_notification.is_read)


class NotificationPreferenceTests(TestCase):
    def test_security_email_is_not_disabled_by_never_frequency(self):
        user = get_user_model().objects.create_user(
            username="notification_preferences",
            email="notification-preferences@example.com",
            password="test-password",
        )
        preferences = NotificationPreferences.objects.create(
            user=user,
            email_frequency="never",
        )

        self.assertTrue(preferences.should_send_email("security"))
        self.assertFalse(preferences.should_send_email("comment"))


class AuthenticationRouteTests(TestCase):
    def test_login_required_views_redirect_to_the_users_login_form(self):
        response = self.client.get(reverse("users:devices"))

        self.assertRedirects(
            response,
            f"{reverse('users:account_login')}?next={reverse('users:devices')}",
        )
