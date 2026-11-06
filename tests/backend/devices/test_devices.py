from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.devices.models import Device, DeviceConfig, DeviceEvent
from apps.devices.models_quota import UserDeviceQuota
from apps.devices.services import (
    DevicePolicyError,
    approve_current_device,
    enforce_device_policy_for_login,
    hash_browser_key,
    mark_device_trusted,
    resolve_or_create_device,
)


class DevicePolicyIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="device_user",
            email="device-user@example.com",
            password="Complex-test-password-123!",
        )
        self.factory = RequestFactory()

    def make_request(self):
        request = self.factory.get(
            "/",
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        request.user = self.user
        request.session = SessionStore()
        request.META["REMOTE_ADDR"] = "203.0.113.10"
        return request

    def test_device_registration_is_idempotent_for_the_same_identity(self):
        request = self.make_request()

        first_device, first_is_new, _ = resolve_or_create_device(
            request, self.user, service_name="login"
        )
        second_device, second_is_new, _ = resolve_or_create_device(
            request, self.user, service_name="login"
        )

        self.assertTrue(first_is_new)
        self.assertFalse(second_is_new)
        assert first_device is not None
        assert second_device is not None
        self.assertEqual(first_device.pk, second_device.pk)
        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)

    def test_browser_key_is_authoritative_across_user_agent_and_ip_changes(self):
        browser_key = "a" * 43
        first_request = self.make_request()
        first_request.COOKIES["mobofirm_device"] = browser_key
        first, _, _ = resolve_or_create_device(
            first_request, self.user, service_name="login"
        )

        second_request = self.factory.get(
            "/",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/605.1",
        )
        second_request.COOKIES["mobofirm_device"] = browser_key
        second_request.user = self.user
        second_request.session = SessionStore()
        second_request.META["REMOTE_ADDR"] = "198.51.100.8"
        second, is_new, _ = resolve_or_create_device(
            second_request, self.user, service_name="login"
        )

        assert first is not None
        assert second is not None
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(is_new)
        self.assertEqual(second.os_family, "iOS")
        self.assertNotEqual(second.browser_key_hash, browser_key)
        self.assertEqual(second.browser_key_hash, hash_browser_key(browser_key))

    def test_different_browser_keys_create_separate_pending_profiles(self):
        devices = []
        for browser_key in ("b" * 43, "c" * 43):
            request = self.make_request()
            request.COOKIES["mobofirm_device"] = browser_key
            device, is_new, _ = resolve_or_create_device(
                request, self.user, service_name="login"
            )
            assert device is not None
            devices.append(device)
            self.assertTrue(is_new)
            self.assertFalse(device.is_trusted)

        self.assertNotEqual(devices[0].pk, devices[1].pk)
        self.assertEqual(Device.objects.filter(user=self.user).count(), 2)

    def test_device_defaults_to_untrusted(self):
        device = Device.objects.create(
            user=self.user,
            os_fingerprint="default-untrusted-device",
        )

        self.assertFalse(device.is_trusted)

    def test_repeated_login_signals_register_only_one_device(self):
        request = self.make_request()

        user_logged_in.send(sender=self.user.__class__, request=request, user=self.user)
        user_logged_in.send(sender=self.user.__class__, request=request, user=self.user)

        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)

    def test_successful_login_does_not_create_a_policy_violation(self):
        allowed, _ = enforce_device_policy_for_login(self.make_request(), self.user)

        self.assertTrue(allowed)
        self.assertTrue(
            DeviceEvent.objects.filter(
                user=self.user, event_type="login", success=True
            ).exists()
        )
        self.assertFalse(
            DeviceEvent.objects.filter(
                user=self.user, event_type="policy_violation"
            ).exists()
        )

    @patch("apps.users.services.notifications.send_notification")
    def test_new_device_notification_uses_readable_profile_not_fingerprint(
        self, send_notification
    ):
        request = self.make_request()
        request.COOKIES["mobofirm_device"] = "f" * 43

        allowed, result = enforce_device_policy_for_login(request, self.user)

        self.assertTrue(allowed)
        self.assertTrue(result["is_new"])
        message = send_notification.call_args.args[2]
        self.assertIn("Windows computer · Chrome", message)
        self.assertIn("203.0.113.10", message)
        self.assertNotIn("f" * 64, message)
        self.assertNotIn("fingerprint", message.lower())

    def test_strict_policy_does_not_deadlock_new_device_approval(self):
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = True
        config.save(update_fields=["strict_new_device_login"])

        allowed, result = enforce_device_policy_for_login(
            self.make_request(), self.user
        )

        self.assertTrue(allowed)
        self.assertTrue(result["is_new"])
        self.assertFalse(result["device"].is_trusted)
        self.assertTrue(
            DeviceEvent.objects.filter(
                user=self.user,
                event_type="login",
                success=True,
            ).exists()
        )
        self.assertFalse(
            DeviceEvent.objects.filter(user=self.user, event_type="policy_violation")
            .filter(success=False)
            .exists()
        )

    def test_trusting_a_blocked_device_does_not_unblock_it(self):
        device = Device.objects.create(
            user=self.user,
            os_fingerprint="blocked-device",
            is_trusted=False,
            is_blocked=True,
        )

        self.assertTrue(mark_device_trusted(device.pk, self.user.pk))

        device.refresh_from_db()
        self.assertTrue(device.is_trusted)
        self.assertTrue(device.is_blocked)

    def test_owner_cannot_revoke_or_approve_devices_from_the_list_page(self):
        device = Device.objects.create(
            user=self.user,
            os_fingerprint="pending-device",
            is_trusted=False,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("users:devices"),
            {"action": "trust", "device_id": str(device.pk)},
        )

        self.assertEqual(response.status_code, 405)
        device.refresh_from_db()
        self.assertFalse(device.is_trusted)

    def test_devices_app_owns_the_canonical_management_page(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("devices:my_devices"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registered browsers")
        self.assertContains(response, "Approved browsers")

    def test_approval_is_bound_to_the_current_browser_key_and_respects_capacity(self):
        config = DeviceConfig.get_solo()
        config.max_devices_default = 1
        config.save(update_fields=["max_devices_default"])
        Device.objects.create(
            user=self.user,
            browser_key_hash=hash_browser_key("d" * 43),
            is_trusted=True,
        )
        current_key = "e" * 43
        request = self.make_request()
        request.COOKIES["mobofirm_device"] = current_key
        pending, _, _ = resolve_or_create_device(request, self.user)
        assert pending is not None

        with self.assertRaises(DevicePolicyError) as error:
            approve_current_device(request, self.user)

        self.assertEqual(error.exception.reason, "limit_reached")
        pending.refresh_from_db()
        self.assertFalse(pending.is_trusted)

    def test_device_quota_is_registered_in_django_admin(self):
        self.assertTrue(admin.site.is_registered(UserDeviceQuota))

    def test_owner_cannot_trust_another_users_device(self):
        other_user = get_user_model().objects.create_user(
            username="another_device_user",
            email="another-device-user@example.com",
            password="test-password",
        )
        device = Device.objects.create(
            user=other_user,
            os_fingerprint="another-users-device",
            is_trusted=False,
        )
        self.client.force_login(self.user)

        self.client.post(
            reverse("users:devices"),
            {"action": "trust", "device_id": str(device.pk)},
        )

        device.refresh_from_db()
        self.assertFalse(device.is_trusted)

    def test_verified_mfa_allows_new_device_policy_to_continue(self):
        config = DeviceConfig.get_solo()
        config.require_mfa_on_new_device = True
        config.save(update_fields=["require_mfa_on_new_device"])

        allowed, result = enforce_device_policy_for_login(
            self.make_request(),
            self.user,
            mfa_verified=True,
        )

        self.assertTrue(allowed)
        self.assertTrue(result["is_new"])
