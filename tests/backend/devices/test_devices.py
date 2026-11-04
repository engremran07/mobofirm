from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.test import RequestFactory, TestCase

from apps.devices.models import Device, DeviceConfig, DeviceEvent
from apps.devices.services import (
    DevicePolicyError,
    enforce_device_policy_for_login,
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
        request.session = {}
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
        self.assertEqual(first_device.pk, second_device.pk)
        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)

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

    def test_untrusted_new_device_is_denied_and_audited(self):
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = True
        config.save(update_fields=["strict_new_device_login"])

        with self.assertRaises(DevicePolicyError):
            enforce_device_policy_for_login(self.make_request(), self.user)

        self.assertTrue(
            DeviceEvent.objects.filter(
                user=self.user,
                event_type="login",
                success=False,
                reason="untrusted_new_device",
            ).exists()
        )
        self.assertTrue(
            DeviceEvent.objects.filter(
                user=self.user, event_type="policy_violation", success=False
            ).exists()
        )
