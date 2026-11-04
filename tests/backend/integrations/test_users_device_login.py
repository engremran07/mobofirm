from types import SimpleNamespace
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.devices.models import Device, DeviceConfig
from apps.devices.services import DevicePolicyError, enforce_device_policy_for_login
from apps.users.mfa import TOTPService
from apps.users.models import MFADevice
from apps.users.services.rate_limit import get_attempt_count


class UserDeviceLoginContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="device_login_user",
            email="device-login-user@example.com",
            password="test-password",
            email_verified_at=None,
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            primary=True,
            verified=True,
        )

    def test_device_policy_denial_does_not_create_an_authenticated_session(self):
        enforcement = Mock(side_effect=DevicePolicyError("blocked_device"))
        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            response = self.client.post(
                reverse("users:account_login"),
                {"login": self.user.email, "password": "test-password"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        enforcement.assert_called_once()

    def test_false_device_policy_result_does_not_create_an_authenticated_session(self):
        enforcement = Mock(return_value=(False, {}))
        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            response = self.client.post(
                reverse("users:account_login"),
                {"login": self.user.email, "password": "test-password"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        enforcement.assert_called_once()

    def test_successful_logins_do_not_consume_the_failed_attempt_budget(self):
        enforcement = Mock(return_value=(True, {}))
        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            for _ in range(6):
                response = self.client.post(
                    reverse("users:account_login"),
                    {"login": self.user.email, "password": "test-password"},
                )
                self.assertEqual(response.status_code, 302)
                self.client.logout()

        self.assertEqual(get_attempt_count("login:127.0.0.1"), 0)

    def test_failed_logins_exhaust_the_budget_before_a_valid_login(self):
        login_url = reverse("users:account_login")
        for _ in range(5):
            self.client.post(
                login_url,
                {"login": self.user.email, "password": "incorrect-password"},
            )

        self.assertEqual(get_attempt_count("login:127.0.0.1"), 5)

        response = self.client.post(
            login_url,
            {"login": self.user.email, "password": "test-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_successful_login_resets_failed_attempts(self):
        login_url = reverse("users:account_login")
        self.client.post(
            login_url,
            {"login": self.user.email, "password": "incorrect-password"},
        )
        self.assertEqual(get_attempt_count("login:127.0.0.1"), 1)

        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(
                enforce_device_policy_for_login=Mock(return_value=(True, {}))
            ),
        ):
            response = self.client.post(
                login_url,
                {"login": self.user.email, "password": "test-password"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_attempt_count("login:127.0.0.1"), 0)

    def test_device_mfa_challenge_authenticates_only_after_totp_verification(self):
        secret = TOTPService.generate_secret()
        device = MFADevice.objects.create(
            user=self.user,
            device_type="totp",
            name="Test authenticator",
            is_primary=True,
            is_active=True,
        )
        device.set_secret(secret)
        device.save(update_fields=["secret_encrypted"])
        enforcement = Mock(side_effect=DevicePolicyError("mfa_required"))

        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            login_response = self.client.post(
                reverse("users:account_login"),
                {"login": self.user.email, "password": "test-password"},
            )

        self.assertRedirects(
            login_response,
            reverse("users:mfa_verify"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

        invalid_response = self.client.post(
            reverse("users:mfa_verify"),
            {"code": "notcode"},
        )
        self.assertRedirects(
            invalid_response,
            reverse("users:mfa_verify"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", self.client.session)

        verification_response = self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        self.assertRedirects(
            verification_response,
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            str(self.client.session["_auth_user_id"]),
            str(self.user.pk),
        )
        self.assertEqual(
            self.client.session["mfa_verified_user_id"],
            str(self.user.pk),
        )

    def test_enrolled_user_requires_totp_even_when_device_policy_allows_login(self):
        secret = TOTPService.generate_secret()
        device = MFADevice.objects.create(
            user=self.user,
            device_type="totp",
            name="Test authenticator",
            is_primary=True,
            is_active=True,
        )
        device.set_secret(secret)
        device.save(update_fields=["secret_encrypted"])
        enforcement = Mock(return_value=(True, {}))

        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            login_response = self.client.post(
                reverse("users:account_login"),
                {"login": self.user.email, "password": "test-password"},
            )

        self.assertRedirects(
            login_response,
            reverse("users:mfa_verify"),
            fetch_redirect_response=False,
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        enforcement.assert_called_once()

        verify_response = self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        self.assertRedirects(
            verify_response,
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            str(self.client.session["_auth_user_id"]),
            str(self.user.pk),
        )

    def test_repeated_untrusted_device_attempts_remain_denied(self):
        config = DeviceConfig.get_solo()
        config.strict_new_device_login = True
        config.save(update_fields=["strict_new_device_login"])

        request = RequestFactory().get(
            "/users/login/",
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0",
        )
        request.user = self.user
        request.session = {}
        request.META["REMOTE_ADDR"] = "203.0.113.10"

        for _ in range(2):
            with self.assertRaises(DevicePolicyError) as error:
                enforce_device_policy_for_login(request, self.user)
            self.assertEqual(error.exception.reason, "untrusted_new_device")

        self.assertEqual(Device.objects.filter(user=self.user).count(), 1)
        self.assertFalse(Device.objects.get(user=self.user).is_trusted)
