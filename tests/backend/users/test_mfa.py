from __future__ import annotations

from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.users.mfa import TOTPService
from apps.users.models import MFADevice, UsersSettings
from apps.users.services.mfa import verify_mfa_code


class MultiFactorAuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="mfa_member",
            email="mfa-member@example.com",
            password="A-valid-password-983!",
            profile_completed=True,
            email_verified_at=timezone.now(),
        )
        EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            primary=True,
            verified=True,
        )
        self.client.force_login(self.user)

    def create_totp_device(self) -> tuple[MFADevice, str]:
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
        return device, secret

    def test_setup_verifies_authenticator_and_shows_recovery_codes_once(self):
        response = self.client.get(reverse("users:mfa_setup"))

        self.assertEqual(response.status_code, 200)
        secret = self.client.session["pending_mfa_setup_secret"]
        code = TOTPService.generate_current_code(secret)
        response = self.client.post(
            reverse("users:mfa_setup"),
            {"code": code},
        )

        self.assertRedirects(
            response,
            reverse("users:mfa_backup_codes"),
            fetch_redirect_response=False,
        )
        totp_device = MFADevice.objects.get(user=self.user, device_type="totp")
        self.assertTrue(totp_device.is_active)
        self.assertNotEqual(totp_device.secret_encrypted, secret)
        self.assertEqual(totp_device.get_secret(), secret)
        self.assertEqual(
            MFADevice.objects.filter(user=self.user, device_type="backup").count(),
            1,
        )

        codes_response = self.client.get(reverse("users:mfa_backup_codes"))
        self.assertEqual(len(codes_response.context["backup_codes"]), 10)
        self.assertNotIn("mfa_backup_codes_once", self.client.session)
        self.assertRedirects(
            self.client.get(reverse("users:mfa_backup_codes")),
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )

    def test_setup_rejects_invalid_code_without_creating_a_device(self):
        self.client.get(reverse("users:mfa_setup"))

        response = self.client.post(
            reverse("users:mfa_setup"),
            {"code": "notcode"},
        )

        self.assertRedirects(
            response,
            reverse("users:mfa_setup"),
            fetch_redirect_response=False,
        )
        self.assertFalse(MFADevice.objects.filter(user=self.user).exists())

    def test_required_mfa_redirects_to_setup_then_protects_dashboard(self):
        settings_obj = UsersSettings.get_solo()
        settings_obj.require_mfa = True
        settings_obj.save(update_fields=["require_mfa"])

        response = self.client.get(reverse("users:dashboard"))
        self.assertRedirects(
            response,
            reverse("users:mfa_setup"),
            fetch_redirect_response=False,
        )

        self.client.get(reverse("users:mfa_setup"))
        secret = self.client.session["pending_mfa_setup_secret"]
        setup_response = self.client.post(
            reverse("users:mfa_setup"),
            {"code": TOTPService.generate_current_code(secret)},
        )
        self.assertRedirects(
            setup_response,
            reverse("users:mfa_backup_codes"),
            fetch_redirect_response=False,
        )
        self.client.get(reverse("users:mfa_backup_codes"))
        self.assertEqual(
            self.client.get(reverse("users:dashboard")).status_code,
            200,
        )

    def test_existing_authenticator_requires_verification_for_protected_pages(self):
        _, secret = self.create_totp_device()

        response = self.client.get(reverse("users:dashboard"))
        self.assertRedirects(
            response,
            reverse("users:mfa_verify"),
            fetch_redirect_response=False,
        )

        code = TOTPService.generate_current_code(secret)
        response = self.client.post(reverse("users:mfa_verify"), {"code": code})

        self.assertRedirects(
            response,
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.session["mfa_verified_user_id"],
            str(self.user.pk),
        )
        self.assertEqual(self.client.get(reverse("users:dashboard")).status_code, 200)

    def test_totp_code_cannot_be_replayed(self):
        device, secret = self.create_totp_device()
        code = TOTPService.generate_current_code(secret)

        self.assertTrue(device.verify_code(code))
        device.refresh_from_db()
        self.assertFalse(device.verify_code(code))

    def test_backup_code_is_consumed_after_one_successful_use(self):
        backup_device = MFADevice.objects.create(
            user=self.user,
            device_type="backup",
            name="Recovery codes",
        )
        backup_code = backup_device.generate_backup_codes()[0]

        self.assertTrue(verify_mfa_code(self.user, backup_code))
        self.assertFalse(verify_mfa_code(self.user, backup_code))

    def test_authenticator_attempts_are_rate_limited(self):
        _, secret = self.create_totp_device()

        for _ in range(5):
            response = self.client.post(
                reverse("users:mfa_verify"),
                {"code": "notcode"},
            )
            self.assertRedirects(
                response,
                reverse("users:mfa_verify"),
                fetch_redirect_response=False,
            )

        limited_response = self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )
        self.assertRedirects(
            limited_response,
            reverse("users:mfa_verify"),
            fetch_redirect_response=False,
        )
        self.assertContains(
            self.client.get(reverse("users:mfa_verify")),
            "Too many verification attempts",
        )
        self.assertNotIn("mfa_verified_user_id", self.client.session)

    def test_optional_mfa_can_be_disabled_after_code_verification(self):
        _, secret = self.create_totp_device()
        backup_device = MFADevice.objects.create(
            user=self.user,
            device_type="backup",
            name="Recovery codes",
        )
        backup_code = backup_device.generate_backup_codes()[0]
        self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        response = self.client.post(
            reverse("users:mfa_disable"),
            {"code": backup_code},
        )

        self.assertRedirects(
            response,
            reverse("users:dashboard"),
            fetch_redirect_response=False,
        )
        self.assertFalse(MFADevice.objects.filter(user=self.user).exists())

    def test_required_mfa_cannot_be_disabled(self):
        _, secret = self.create_totp_device()
        settings_obj = UsersSettings.get_solo()
        settings_obj.require_mfa = True
        settings_obj.save(update_fields=["require_mfa"])
        session = self.client.session
        session["mfa_verified_user_id"] = str(self.user.pk)
        session.save()

        response = self.client.post(
            reverse("users:mfa_disable"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(MFADevice.objects.filter(user=self.user).exists())

    @override_settings(REQUIRE_MFA=True)
    def test_global_mfa_requirement_prevents_disabling_authenticator(self):
        _, secret = self.create_totp_device()
        session = self.client.session
        session["mfa_verified_user_id"] = str(self.user.pk)
        session.save()

        verify_response = self.client.get(reverse("users:mfa_verify"))
        self.assertEqual(verify_response.status_code, 200)
        self.assertFalse(verify_response.context["can_disable"])

        response = self.client.post(
            reverse("users:mfa_disable"),
            {"code": TOTPService.generate_current_code(secret)},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(MFADevice.objects.filter(user=self.user).exists())

    def test_api_login_requires_and_verifies_totp_for_enrolled_users(self):
        _, secret = self.create_totp_device()
        api_client = APIClient()
        url = reverse("users:api_login")
        valid_code = TOTPService.generate_current_code(secret)
        bad_code = "notcode"
        credentials = {
            "identifier": self.user.email,
            "password": "A-valid-password-983!",
        }

        missing_code = api_client.post(url, credentials, format="json")
        self.assertEqual(missing_code.status_code, 403)
        self.assertTrue(missing_code.data["mfa_required"])

        invalid_response = api_client.post(
            url,
            {**credentials, "totp_code": bad_code},
            format="json",
        )
        self.assertEqual(invalid_response.status_code, 401)

        valid_response = api_client.post(
            url,
            {**credentials, "totp_code": valid_code},
            format="json",
        )
        self.assertEqual(valid_response.status_code, 200)
        self.assertIn("token", valid_response.data)

    def test_api_login_requires_setup_when_site_requires_mfa(self):
        settings_obj = UsersSettings.get_solo()
        settings_obj.require_mfa = True
        settings_obj.save(update_fields=["require_mfa"])

        response = APIClient().post(
            reverse("users:api_login"),
            {
                "identifier": self.user.email,
                "password": "A-valid-password-983!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data["mfa_setup_required"])
        self.assertFalse(
            MFADevice.objects.filter(user=self.user, is_active=True).exists()
        )

    @override_settings(REQUIRE_MFA=True)
    def test_api_login_requires_setup_when_global_mfa_is_enabled(self):
        response = APIClient().post(
            reverse("users:api_login"),
            {
                "identifier": self.user.email,
                "password": "A-valid-password-983!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data["mfa_setup_required"])
        self.assertFalse(
            MFADevice.objects.filter(user=self.user, is_active=True).exists()
        )

    @override_settings(
        SOCIALACCOUNT_PROVIDERS={
            "google": {
                "APP": {
                    "client_id": "test-client-id",
                    "secret": "test-client-secret",
                }
            }
        }
    )
    def test_google_api_login_requires_totp_for_enrolled_users(self):
        _, secret = self.create_totp_device()
        provider = Mock()
        provider.verify_token.return_value = object()

        def authenticate(request, _sociallogin):
            request.user = self.user

        with (
            patch(
                "apps.users.api_auth.get_social_adapter",
                return_value=Mock(get_provider=Mock(return_value=provider)),
            ),
            patch(
                "apps.users.api_auth.complete_social_login",
                side_effect=authenticate,
            ),
        ):
            api_client = APIClient()
            url = reverse("users:api_google_login")
            missing_code = api_client.post(
                url,
                {"id_token": "a-google-id-token"},
                format="json",
            )
            self.assertEqual(missing_code.status_code, 403)
            self.assertTrue(missing_code.data["mfa_required"])

            valid_code = api_client.post(
                url,
                {
                    "id_token": "a-google-id-token",
                    "totp_code": TOTPService.generate_current_code(secret),
                },
                format="json",
            )

        self.assertEqual(valid_code.status_code, 200)
        self.assertIn("token", valid_code.data)

    @override_settings(
        REQUIRE_MFA=True,
        SOCIALACCOUNT_PROVIDERS={
            "google": {
                "APP": {
                    "client_id": "test-client-id",
                    "secret": "test-client-secret",
                }
            }
        },
    )
    def test_google_api_login_requires_setup_when_global_mfa_is_enabled(self):
        provider = Mock()
        provider.verify_token.return_value = object()

        def authenticate(request, _sociallogin):
            request.user = self.user

        with (
            patch(
                "apps.users.api_auth.get_social_adapter",
                return_value=Mock(get_provider=Mock(return_value=provider)),
            ),
            patch(
                "apps.users.api_auth.complete_social_login",
                side_effect=authenticate,
            ),
        ):
            response = APIClient().post(
                reverse("users:api_google_login"),
                {"id_token": "a-google-id-token"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.data["mfa_setup_required"])
