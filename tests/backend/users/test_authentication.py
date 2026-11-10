from __future__ import annotations

import re
import smtplib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from allauth.account.forms import LoginForm
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.devices.services import DevicePolicyError


class UserAuthenticationApiTests(TestCase):
    client: APIClient

    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.client = APIClient()

    def create_user(
        self,
        *,
        email: str = "member@example.com",
        username: str = "member",
        password: str = "A-valid-password-983!",
        verified: bool = True,
        profile_completed: bool = True,
    ):
        user = get_user_model().objects.create_user(
            email=email,
            username=username,
            password=password,
            profile_completed=profile_completed,
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=verified,
        )
        return user

    def test_api_login_accepts_username_and_returns_revocable_token(self):
        user = self.create_user()

        response = self.client.post(
            reverse("users:api_login"),
            {"identifier": user.username, "password": "A-valid-password-983!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        token = Token.objects.get(user=user)
        self.assertEqual(response.data, {"token": token.key, "token_type": "Token"})

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        current_user = self.client.get(reverse("users:api_current_user"))
        self.assertEqual(current_user.status_code, 200)
        self.assertEqual(current_user.data["username"], user.username)
        self.assertTrue(current_user.data["email_verified"])

        logout = self.client.post(reverse("users:api_logout"), format="json")
        self.assertEqual(logout.status_code, 204)
        self.assertFalse(Token.objects.filter(user=user).exists())
        self.assertEqual(
            self.client.get(reverse("users:api_current_user")).status_code,
            401,
        )

    def test_api_login_rejects_bad_credentials_without_creating_a_token(self):
        user = self.create_user()

        response = self.client.post(
            reverse("users:api_login"),
            {"identifier": user.email, "password": "incorrect-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Token.objects.filter(user=user).exists())

    def test_api_login_rejects_device_policy_denial_without_creating_a_token(self):
        user = self.create_user()
        enforcement = Mock(side_effect=DevicePolicyError("blocked_device"))

        with patch(
            "apps.users.api_auth.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=enforcement),
        ):
            response = self.client.post(
                reverse("users:api_login"),
                {"identifier": user.email, "password": "A-valid-password-983!"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Token.objects.filter(user=user).exists())
        enforcement.assert_called_once()

    def test_api_login_requires_verified_email_and_completed_profile(self):
        unverified = self.create_user(
            email="unverified@example.com",
            username="unverified",
            verified=False,
        )
        incomplete = self.create_user(
            email="incomplete@example.com",
            username="incomplete",
            profile_completed=False,
        )

        unverified_response = self.client.post(
            reverse("users:api_login"),
            {"identifier": unverified.email, "password": "A-valid-password-983!"},
            format="json",
        )
        incomplete_response = self.client.post(
            reverse("users:api_login"),
            {"identifier": incomplete.username, "password": "A-valid-password-983!"},
            format="json",
        )

        self.assertEqual(unverified_response.status_code, 403)
        self.assertTrue(unverified_response.data["email_confirmation_required"])
        self.assertEqual(incomplete_response.status_code, 403)
        self.assertTrue(incomplete_response.data["profile_completion_required"])
        self.assertFalse(Token.objects.filter(user=unverified).exists())
        self.assertFalse(Token.objects.filter(user=incomplete).exists())

    def test_web_login_rejects_accounts_before_email_confirmation(self):
        user = self.create_user(
            email="awaiting-confirmation@example.com",
            username="awaiting_confirmation",
            verified=False,
        )

        device_policy = Mock(return_value=(True, {}))
        with patch(
            "apps.users.views.AppService.get",
            return_value=SimpleNamespace(enforce_device_policy_for_login=device_policy),
        ):
            response = self.client.post(
                reverse("users:account_login"),
                {
                    "login": user.email,
                    "password": "A-valid-password-983!",
                },
            )

        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("account_email_verification_sent")
        )
        device_policy.assert_not_called()
        self.assertFalse(EmailAddress.objects.filter(user=user, verified=True).exists())

    def test_staff_login_also_requires_email_confirmation(self):
        user = self.create_user(
            email="awaiting-admin-confirmation@example.com",
            username="awaiting_admin_confirmation",
            verified=False,
        )
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        response = self.client.post(
            reverse("users:account_login"),
            {
                "login": user.email,
                "password": "A-valid-password-983!",
            },
        )

        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("account_email_verification_sent")
        )

    def test_authenticated_user_can_resend_unverified_email_confirmation(self):
        user = self.create_user(
            email="resend-confirmation@example.com",
            username="resend_confirmation",
            verified=False,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("users:resend_verification"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(user.email, mail.outbox[0].to)

    def test_resending_confirmation_rejects_already_verified_address(self):
        user = self.create_user()
        self.client.force_login(user)

        response = self.client.post(reverse("users:resend_verification"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": False, "error": "already_verified"})
        self.assertFalse(mail.outbox)

    def test_confirmation_resend_is_rate_limited(self):
        user = self.create_user(
            email="resend-limit@example.com",
            username="resend_limit",
            verified=False,
        )
        self.client.force_login(user)

        responses = [
            self.client.post(reverse("users:resend_verification")) for _ in range(4)
        ]

        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 200, 429],
        )
        self.assertEqual(len(mail.outbox), 3)

    def test_hex_code_verifies_email_and_completes_signup_session(self):
        response = self.client.post(
            reverse("users:account_signup"),
            {
                "email": "code-member@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

        code_match = re.search(
            r"one-time verification code.*?\b([0-9A-Z]{6})\b",
            str(mail.outbox[0].body),
            re.DOTALL,
        )
        assert code_match is not None
        code = code_match.group(1)
        verification_state = self.client.session["users_email_verification"]
        self.assertNotIn(code, str(verification_state))
        self.assertNotEqual(
            verification_state["code_hash"],
            code,
        )

        verification_response = self.client.post(
            reverse("users:verify_email"),
            {"code": code},
            follow=False,
        )

        email_address = EmailAddress.objects.get(email="code-member@example.com")
        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)
        self.assertRedirects(
            verification_response,
            reverse("users:tell_us_about_you"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            str(self.client.session["_auth_user_id"]),
            str(email_address.user_id),
        )
        self.assertNotIn("users_email_verification", self.client.session)

    def test_invalid_verification_code_is_bounded_and_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        response = client.get(reverse("users:verify_email"))
        self.assertEqual(response.status_code, 200)

        rejected = client.post(
            reverse("users:verify_email"),
            {"code": "FFFFFF"},
        )
        self.assertEqual(rejected.status_code, 403)

    def test_email_verification_code_expires(self):
        user = self.create_user(
            email="expired-code@example.com",
            username="expired_code",
            verified=False,
        )
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        from apps.users.services.email_verification import (
            CODE_TTL_SECONDS,
            get_pending_email_address,
            issue_email_verification_code,
        )

        request = RequestFactory().get("/")
        request.session = SessionStore()
        code = issue_email_verification_code(request, email_address)
        with patch(
            "apps.users.services.email_verification.time.time",
            return_value=(
                request.session["users_email_verification"]["expires_at"]
                + CODE_TTL_SECONDS
            ),
        ):
            from apps.users.services.email_verification import (
                consume_email_verification_code,
            )

            result, _ = consume_email_verification_code(request, code)

        self.assertEqual(result, "expired")
        self.assertNotIn(
            "code_hash",
            request.session["users_email_verification"],
        )
        self.assertEqual(get_pending_email_address(request), email_address)

    def test_verification_code_attempts_are_limited(self):
        user = self.create_user(
            email="attempt-limit@example.com",
            username="attempt_limit",
            verified=False,
        )
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        from apps.users.services.email_verification import (
            MAX_CODE_ATTEMPTS,
            consume_email_verification_code,
            get_pending_email_address,
            issue_email_verification_code,
        )

        request = RequestFactory().get("/")
        request.session = SessionStore()
        issue_email_verification_code(request, email_address)

        for attempt in range(MAX_CODE_ATTEMPTS):
            result, _ = consume_email_verification_code(request, "FFFFFF")
            self.assertEqual(
                result,
                "rate_limited" if attempt == MAX_CODE_ATTEMPTS - 1 else "invalid",
            )
        self.assertTrue(request.session["users_email_verification"]["rate_limited"])
        self.assertNotIn(
            "code_hash",
            request.session["users_email_verification"],
        )
        self.assertEqual(get_pending_email_address(request), email_address)

    def test_confirmation_delivery_failure_is_not_reported_as_success(self):
        user = self.create_user(
            email="delivery-failure@example.com",
            username="delivery_failure",
            verified=False,
        )
        self.client.force_login(user)

        with patch(
            "allauth.account.models.EmailAddress.send_confirmation",
            side_effect=smtplib.SMTPException("mail transport failed"),
        ):
            response = self.client.post(reverse("users:resend_verification"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"ok": False, "error": "email_unavailable"})

    def test_signup_creates_unverified_user_and_sends_allauth_confirmation(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("users:api_signup"),
                {
                    "email": "new-member@example.com",
                    "password1": "Another-valid-password-983!",
                    "password2": "Another-valid-password-983!",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 202)
        user = get_user_model().objects.get(email="new-member@example.com")
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertTrue(user.manual_signup)
        self.assertEqual(user.signup_method, "manual")
        self.assertTrue(user.has_usable_password())
        self.assertFalse(email_address.verified)
        self.assertTrue(response.data["email_confirmation_required"])
        self.assertEqual(len(mail.outbox), 1)

        duplicate = self.client.post(
            reverse("users:api_signup"),
            {
                "email": "new-member@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
            format="json",
        )
        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(duplicate.data, response.data)

    def test_web_signup_requires_confirmation_before_authentication(self):
        response = self.client.post(
            reverse("users:account_signup"),
            {
                "email": "web-member@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        user = get_user_model().objects.get(email="web-member@example.com")
        email_address = EmailAddress.objects.get(user=user, email=user.email)
        self.assertTrue(user.manual_signup)
        self.assertFalse(email_address.verified)
        self.assertTrue(mail.outbox)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.get(reverse("users:tell_us_about_you")).status_code,
            302,
        )

        confirmation_match = re.search(
            r"/accounts/confirm-email/([^/\s?]+)",
            str(mail.outbox[0].body),
        )
        assert confirmation_match is not None
        confirm_url = reverse(
            "account_confirm_email",
            args=[confirmation_match.group(1)],
        )
        confirmation_response = self.client.post(confirm_url, follow=False)

        email_address.refresh_from_db()
        self.assertTrue(email_address.verified)
        self.assertRedirects(
            confirmation_response,
            reverse("users:tell_us_about_you"),
            fetch_redirect_response=False,
        )
        self.assertTrue(email_address.verified)
        self.assertEqual(
            str(self.client.session["_auth_user_id"]),
            str(user.pk),
        )

    def test_google_sign_in_button_is_always_shown_and_proceeds_only_when_configured(
        self,
    ):
        with override_settings(SOCIALACCOUNT_PROVIDERS={}):
            response = self.client.get(reverse("users:account_login"))
        # Unconfigured: the button is still listed but opens the app-owned notice
        # instead of posting to the provider.
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, "Google sign-in is not set up yet")
        self.assertNotContains(response, 'action="/accounts/google/login/')

        with override_settings(
            SOCIALACCOUNT_PROVIDERS={
                "google": {
                    "APP": {
                        "client_id": "test-client-id",
                        "secret": "test-client-secret",
                    }
                }
            }
        ):
            configured_response = self.client.get(reverse("users:account_login"))

        self.assertContains(configured_response, "Continue with Google")
        self.assertContains(configured_response, "/accounts/google/login/")
        self.assertNotContains(configured_response, "social-unavailable-google")

    def test_signup_rejects_mismatched_passwords_and_hides_duplicate_email(self):
        mismatch = self.client.post(
            reverse("users:api_signup"),
            {
                "email": "new-member@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Not-the-same-password-983!",
            },
            format="json",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertFalse(
            get_user_model().objects.filter(email="new-member@example.com").exists()
        )

        self.create_user()
        duplicate = self.client.post(
            reverse("users:api_signup"),
            {
                "email": "member@example.com",
                "password1": "Another-valid-password-983!",
                "password2": "Another-valid-password-983!",
            },
            format="json",
        )
        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(
            get_user_model().objects.filter(email="member@example.com").count(),
            1,
        )

    def test_password_reset_is_generic_and_sends_mail_for_existing_account(self):
        self.create_user()

        response = self.client.post(
            reverse("users:api_password_reset"),
            {"email": "member@example.com"},
            format="json",
        )
        unknown_response = self.client.post(
            reverse("users:api_password_reset"),
            {"email": "unknown@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(unknown_response.status_code, 202)
        self.assertEqual(response.data, unknown_response.data)
        self.assertTrue(
            any("member@example.com" in message.to for message in mail.outbox)
        )

    def test_google_login_reports_missing_provider_credentials(self):
        response = self.client.post(
            reverse("users:api_google_login"),
            {"id_token": "a-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(Token.objects.exists())

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
    def test_google_login_verifies_token_and_requires_profile_completion(self):
        user = self.create_user(
            email="social@example.com",
            username="social",
            profile_completed=False,
        )
        provider = Mock()
        provider.verify_token.return_value = object()

        def complete_login(request, _sociallogin):
            request.user = user

        with (
            patch(
                "apps.users.api_auth.get_social_adapter",
                return_value=Mock(get_provider=Mock(return_value=provider)),
            ),
            patch(
                "apps.users.api_auth.complete_social_login",
                side_effect=complete_login,
            ),
        ):
            response = self.client.post(
                reverse("users:api_google_login"),
                {"id_token": "a-google-id-token"},
                format="json",
            )

        provider.verify_token.assert_called_once()
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["profile_completion_required"])
        self.assertFalse(Token.objects.filter(user=user).exists())

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
    def test_google_login_rejects_invalid_identity_tokens(self):
        provider = Mock()
        provider.verify_token.side_effect = ValidationError("invalid token")
        with patch(
            "apps.users.api_auth.get_social_adapter",
            return_value=Mock(get_provider=Mock(return_value=provider)),
        ):
            response = self.client.post(
                reverse("users:api_google_login"),
                {"id_token": "invalid"},
                format="json",
            )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Token.objects.exists())

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
    def test_google_login_returns_token_for_verified_completed_user(self):
        user = self.create_user(
            email="social-member@example.com",
            username="social_member",
        )
        provider = Mock()
        provider.verify_token.return_value = object()

        def complete_login(request, _sociallogin):
            request.user = user

        with (
            patch(
                "apps.users.api_auth.get_social_adapter",
                return_value=Mock(get_provider=Mock(return_value=provider)),
            ),
            patch(
                "apps.users.api_auth.complete_social_login",
                side_effect=complete_login,
            ),
        ):
            response = self.client.post(
                reverse("users:api_google_login"),
                {"id_token": "a-google-id-token"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Token.objects.filter(user=user, key=response.data["token"]).exists()
        )


class AllauthAdapterConfigurationTests(TestCase):
    def test_custom_adapters_and_username_login_are_active(self):
        from django.conf import settings

        from apps.users.adapters import (
            CustomAccountAdapter,
            CustomSocialAccountAdapter,
        )

        self.assertEqual(
            settings.ACCOUNT_ADAPTER, "apps.users.adapters.CustomAccountAdapter"
        )
        self.assertEqual(
            settings.SOCIALACCOUNT_ADAPTER,
            "apps.users.adapters.CustomSocialAccountAdapter",
        )
        self.assertEqual(settings.ACCOUNT_LOGIN_METHODS, {"email", "username"})
        self.assertEqual(settings.ACCOUNT_EMAIL_VERIFICATION, "mandatory")
        self.assertTrue(settings.ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION)
        self.assertEqual(
            CustomAccountAdapter.__name__,
            settings.ACCOUNT_ADAPTER.rsplit(".", 1)[-1],
        )
        self.assertEqual(
            CustomSocialAccountAdapter.__name__,
            settings.SOCIALACCOUNT_ADAPTER.rsplit(".", 1)[-1],
        )

    def test_only_verified_email_addresses_are_trusted(self):
        from apps.users.adapters import CustomAccountAdapter

        user = get_user_model().objects.create_user(
            username="verification_member",
            email="verification-member@example.com",
            password="A-valid-password-983!",
        )
        address = EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=False,
        )
        adapter = CustomAccountAdapter()
        request = RequestFactory().get("/")
        request.session = SessionStore()
        request.user = user

        self.assertFalse(adapter.is_email_verified(request, user.email))
        address.verified = True
        address.save(update_fields=["verified"])
        self.assertTrue(adapter.is_email_verified(request, user.email))

        stranger = RequestFactory().get("/")
        stranger.session = SessionStore()
        stranger.user = AnonymousUser()
        self.assertFalse(adapter.is_email_verified(stranger, user.email))


class SocialSignupOnboardingTests(TestCase):
    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_unconfigured_social_provider_redirects_with_actionable_message(self):
        from django.contrib.messages import get_messages

        response = self.client.get("/accounts/google/login/")

        self.assertRedirects(response, reverse("users:account_login"))
        self.assertTrue(
            any(
                "Social sign-in is not configured" in str(message)
                for message in get_messages(response.wsgi_request)
            )
        )

    def test_social_signup_trusts_only_matching_provider_verified_email(self):
        from types import SimpleNamespace

        from apps.users.signals import handle_user_signed_up

        verified_user = get_user_model().objects.create_user(
            username="verified_social",
            email="verified-social@example.com",
            password="test-password-for-social",
        )
        handle_user_signed_up(
            RequestFactory().get("/"),
            verified_user,
            sociallogin=SimpleNamespace(
                email_addresses=[
                    SimpleNamespace(email=verified_user.email, verified=True)
                ]
            ),
        )
        verified_user.refresh_from_db()
        self.assertIsNotNone(verified_user.email_verified_at)

        unverified_user = get_user_model().objects.create_user(
            username="unverified_social",
            email="unverified-social@example.com",
            password="test-password-for-social",
        )
        handle_user_signed_up(
            RequestFactory().get("/"),
            unverified_user,
            sociallogin=SimpleNamespace(
                email_addresses=[
                    SimpleNamespace(email=unverified_user.email, verified=False)
                ]
            ),
        )
        unverified_user.refresh_from_db()
        self.assertIsNone(unverified_user.email_verified_at)

        mismatched_user = get_user_model().objects.create_user(
            username="mismatched_social",
            email="mismatched-social@example.com",
            password="test-password-for-social",
        )
        handle_user_signed_up(
            RequestFactory().get("/"),
            mismatched_user,
            sociallogin=SimpleNamespace(
                email_addresses=[
                    SimpleNamespace(
                        email="different-address@example.com",
                        verified=True,
                    )
                ]
            ),
        )
        mismatched_user.refresh_from_db()
        self.assertIsNone(mismatched_user.email_verified_at)

    def test_social_signup_does_not_log_in_before_allauth_verification(self):
        from types import SimpleNamespace

        from django.contrib.sessions.middleware import SessionMiddleware

        from apps.users.signals import handle_user_signed_up

        user = get_user_model().objects.create_user(
            username="pending_social",
            email="pending-social@example.com",
            password="test-password-for-social",
        )
        request = RequestFactory().get("/")
        SessionMiddleware(lambda _: HttpResponse()).process_request(request)
        request.session.save()

        handle_user_signed_up(
            request,
            user,
            sociallogin=SimpleNamespace(
                email_addresses=[SimpleNamespace(email=user.email, verified=False)]
            ),
        )

        self.assertNotIn("_auth_user_id", request.session)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_onboarding_sets_local_password_and_username_login(self):
        user = get_user_model().objects.create_user(
            username="generated_social_name",
            email="social-onboarding@example.com",
            password=None,
        )
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=True,
        )
        self.client.force_login(user)
        password = "N0vel-Long-Password-Phrase-8472!"

        response = self.client.post(
            reverse("users:tell_us_about_you"),
            {
                "username": "social_member",
                "full_name": "Social Member",
                "country": "US",
                "phone_country_code": "",
                "phone": "",
                "bio": "",
                "website": "",
                "location": "",
                "interests": ["development"],
                "password1": password,
                "password2": password,
            },
        )

        self.assertRedirects(response, reverse("users:dashboard"))
        user.refresh_from_db()
        self.assertEqual(user.username, "social_member")
        self.assertEqual(user.full_name, "Social Member")
        self.assertTrue(user.profile_completed)
        self.assertTrue(user.check_password(password))

        login_form = LoginForm(
            request=RequestFactory().get(reverse("users:account_login")),
            data={"login": user.username, "password": password},
        )
        self.assertTrue(login_form.is_valid(), login_form.errors)
