from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from redis.exceptions import RedisError
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.users.admin import CustomUserAdmin
from apps.users.mfa import TOTPService
from apps.users.models import MFADevice, Notification, UsersSettings
from apps.users.services.rate_limit import (
    allow_action,
    get_attempt_count,
    reset_rate_limit,
)

PASSWORD = "A-valid-password-983!"


def make_user(
    email="hardening@example.com",
    username="hardening",
    *,
    verified=True,
    **extra,
):
    user = get_user_model().objects.create_user(
        email=email,
        username=username,
        password=PASSWORD,
        profile_completed=True,
        email_verified_at=timezone.now() if verified else None,
        **extra,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=verified
    )
    return user


def add_totp(user):
    secret = TOTPService.generate_secret()
    device = MFADevice.objects.create(
        user=user, device_type="totp", name="Authenticator", is_active=True
    )
    device.set_secret(secret)
    device.save(update_fields=["secret_encrypted"])
    return secret


class RateLimiterTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_counter_blocks_after_the_limit_and_resets(self):
        for _ in range(3):
            self.assertTrue(allow_action("t:key", max_attempts=3, window_seconds=60))

        self.assertFalse(allow_action("t:key", max_attempts=3, window_seconds=60))
        self.assertEqual(get_attempt_count("t:key"), 4)
        reset_rate_limit("t:key")
        self.assertTrue(allow_action("t:key", max_attempts=3, window_seconds=60))

    def test_stale_non_integer_values_are_replaced(self):
        cache.set("t:stale", [1.0, 2.0], timeout=60)

        self.assertTrue(allow_action("t:stale", max_attempts=2, window_seconds=60))
        self.assertEqual(get_attempt_count("t:stale"), 1)

    def test_backend_failure_fails_closed_for_security_paths_only(self):
        with patch("apps.users.services.rate_limit.cache") as broken:
            broken.add.side_effect = RedisError("down")

            self.assertFalse(allow_action("t:k", fail_open=False))
            self.assertTrue(allow_action("t:k", fail_open=True))

    def test_empty_key_is_rejected(self):
        self.assertFalse(allow_action(""))


class DrfAuthenticationSurfaceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def test_default_authentication_is_session_only(self):
        from django.conf import settings

        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
            ["rest_framework.authentication.SessionAuthentication"],
        )
        self.assertIn(
            "rest_framework.throttling.AnonRateThrottle",
            settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"],
        )

    def test_basic_authentication_no_longer_grants_api_access(self):
        import base64

        credentials = base64.b64encode(
            f"{self.user.email}:{PASSWORD}".encode()
        ).decode()
        response = APIClient().get(
            reverse("users:api_current_user"),
            HTTP_AUTHORIZATION=f"Basic {credentials}",
        )

        self.assertIn(response.status_code, {401, 403})

    def test_unlisted_endpoints_ignore_token_credentials(self):
        staff = make_user("staff-api@example.com", "staffapi", is_staff=True)
        token = Token.objects.create(user=staff)

        response = APIClient().get(
            reverse("devices:behavior-insights-list"),
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        self.assertIn(response.status_code, {401, 403})

    def test_login_issues_a_fresh_token_each_time_and_drops_the_old_one(self):
        client = APIClient()
        payload = {"identifier": self.user.email, "password": PASSWORD}

        first = client.post(reverse("users:api_login"), payload, format="json")
        second = client.post(reverse("users:api_login"), payload, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.data["token"], second.data["token"])
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)

    def test_expired_tokens_are_rejected_and_deleted(self):
        token = Token.objects.create(user=self.user)
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(days=30)
        )

        response = APIClient().get(
            reverse("users:api_current_user"),
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    def test_token_stops_working_when_the_email_is_no_longer_verified(self):
        token = Token.objects.create(user=self.user)
        EmailAddress.objects.filter(user=self.user).update(verified=False)

        response = APIClient().get(
            reverse("users:api_current_user"),
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        self.assertEqual(response.status_code, 401)

    def test_password_change_revokes_api_tokens(self):
        from allauth.account.signals import password_changed

        Token.objects.create(user=self.user)

        password_changed.send(sender=type(self.user), request=None, user=self.user)

        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_cleanup_command_removes_tokens_past_their_lifetime(self):
        from django.core.management import call_command

        old = Token.objects.create(user=self.user)
        Token.objects.filter(pk=old.pk).update(
            created=timezone.now() - timedelta(days=30)
        )
        other = make_user("other-token@example.com", "othertoken")
        fresh = Token.objects.create(user=other)

        call_command("cleanup_old_data", stdout=Mock())

        self.assertFalse(Token.objects.filter(pk=old.pk).exists())
        self.assertTrue(Token.objects.filter(pk=fresh.pk).exists())


class ApiMfaBruteForceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("api-mfa@example.com", "apimfa")
        self.secret = add_totp(self.user)

    def login(self, code, **extra):
        return APIClient().post(
            reverse("users:api_login"),
            {"identifier": self.user.email, "password": PASSWORD, "totp_code": code},
            format="json",
            **extra,
        )

    def test_wrong_codes_are_limited_then_a_valid_code_is_still_refused(self):
        statuses = [self.login("000000").status_code for _ in range(5)]
        locked = self.login(TOTPService.generate_current_code(self.secret))

        self.assertEqual(statuses, [401] * 5)
        self.assertEqual(locked.status_code, 429)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_the_limit_is_per_client_address(self):
        for _ in range(5):
            self.login("000000")

        other_client = self.login(
            TOTPService.generate_current_code(self.secret),
            REMOTE_ADDR="198.51.100.77",
        )

        self.assertEqual(other_client.status_code, 200)

    def test_throttle_ignores_a_spoofed_forwarded_for_header(self):
        for index in range(5):
            self.login("000000", HTTP_X_FORWARDED_FOR=f"9.9.9.{index}")

        spoofed = self.login(
            TOTPService.generate_current_code(self.secret),
            HTTP_X_FORWARDED_FOR="9.9.9.200",
        )

        self.assertEqual(spoofed.status_code, 429)


@override_settings(
    SOCIALACCOUNT_PROVIDERS={
        "google": {"APP": {"client_id": "id", "secret": "secret"}},
    }
)
class GoogleApiHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("google-hardening@example.com", "googlehard")

    def post_google(self, totp_code=None):
        provider = Mock()
        provider.verify_token.return_value = object()

        def complete_login(request, _sociallogin):
            request.user = self.user

        payload = {"id_token": "token"}
        if totp_code:
            payload["totp_code"] = totp_code
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
            return APIClient().post(
                reverse("users:api_google_login"), payload, format="json"
            )

    def test_device_policy_is_enforced_before_a_token_is_issued(self):
        from apps.devices.services import DevicePolicyError

        enforcement = Mock(side_effect=DevicePolicyError("blocked_device"))
        with patch(
            "apps.users.api_auth.AppService.get",
            return_value=Mock(enforce_device_policy_for_login=enforcement),
        ):
            response = self.post_google()

        self.assertEqual(response.status_code, 403)
        enforcement.assert_called_once()
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_google_totp_attempts_are_limited(self):
        add_totp(self.user)
        statuses = [self.post_google("000000").status_code for _ in range(5)]

        self.assertEqual(statuses, [401] * 5)
        self.assertEqual(self.post_google("000000").status_code, 429)

    def test_no_browser_session_is_left_behind(self):
        with patch(
            "apps.users.api_auth.AppService.get",
            return_value=Mock(
                enforce_device_policy_for_login=Mock(return_value=(True, {}))
            ),
        ):
            response = self.post_google()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)


class StockAllauthRoutesTests(TestCase):
    def test_stock_login_and_signup_redirect_to_the_app_views(self):
        login = self.client.get("/accounts/login/?next=/users/dashboard/")
        signup = self.client.get("/accounts/signup/")

        self.assertEqual(login.status_code, 302)
        self.assertTrue(login["Location"].startswith(reverse("users:account_login")))
        self.assertIn("next=", login["Location"])
        self.assertEqual(signup.status_code, 302)
        self.assertEqual(signup["Location"], reverse("users:account_signup"))

    def test_stock_google_token_endpoint_is_closed(self):
        response = self.client.post("/accounts/google/login/token/", {})

        self.assertEqual(response.status_code, 404)


class WebLoginHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("web-login@example.com", "weblogin")

    @override_settings(
        RECAPTCHA_ENABLED=True, RECAPTCHA_MODE="v2", RECAPTCHA_PRIVATE_KEY="secret"
    )
    def test_recaptcha_is_required_when_enabled_instead_of_skipped(self):
        response = self.client.post(
            reverse("users:account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "reCAPTCHA verification is required.")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(
        RECAPTCHA_ENABLED=True, RECAPTCHA_MODE="v2", RECAPTCHA_PRIVATE_KEY="secret"
    )
    def test_failed_recaptcha_blocks_sign_in(self):
        with patch(
            "apps.users.views.verify_recaptcha",
            return_value={"ok": False, "errors": ["invalid-input-response"]},
        ):
            response = self.client.post(
                reverse("users:account_login"),
                {
                    "login": self.user.email,
                    "password": PASSWORD,
                    "g-recaptcha-response": "bad",
                },
            )

        self.assertContains(response, "reCAPTCHA verification failed")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_remember_me_choice_controls_the_session_lifetime(self):
        client = Client()
        response = client.post(
            reverse("users:account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.session.get_expire_at_browser_close())

    def test_signup_attempts_are_rate_limited_per_client_address(self):
        with override_settings(SIGNUP_ATTEMPTS_PER_HOUR=2):
            statuses = []
            for index in range(3):
                response = self.client.post(
                    reverse("users:account_signup"),
                    {
                        "email": f"new{index}@example.com",
                        "password1": PASSWORD,
                        "password2": PASSWORD,
                    },
                )
                statuses.append(response.status_code)

        self.assertEqual(statuses[2], 200)
        self.assertContains(response, "Too many sign-up attempts")
        self.assertFalse(
            get_user_model().objects.filter(email="new2@example.com").exists()
        )

    @override_settings(ENABLE_SIGNUP=False)
    def test_environment_switch_closes_signup(self):
        response = self.client.post(
            reverse("users:account_signup"),
            {
                "email": "closed@example.com",
                "password1": PASSWORD,
                "password2": PASSWORD,
            },
        )

        self.assertContains(response, "sign up is currently closed")
        self.assertFalse(
            get_user_model().objects.filter(email="closed@example.com").exists()
        )

    def test_password_reset_toggle_is_enforced(self):
        settings_obj = UsersSettings.get_solo()
        settings_obj.enable_password_reset = False
        settings_obj.save(update_fields=["enable_password_reset"])
        mail.outbox.clear()

        response = self.client.post(
            "/accounts/password/reset/", {"email": self.user.email}
        )

        self.assertRedirects(
            response, reverse("users:account_login"), fetch_redirect_response=False
        )
        self.assertEqual(mail.outbox, [])

    def test_password_reset_still_works_when_enabled(self):
        mail.outbox.clear()

        response = self.client.post(
            "/accounts/password/reset/", {"email": self.user.email}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)


class MfaSessionHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("mfa-hard@example.com", "mfahard")
        self.secret = add_totp(self.user)

    def test_mfa_disable_requires_a_verified_session(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("users:mfa_disable"),
            {"code": TOTPService.generate_current_code(self.secret)},
        )

        self.assertRedirects(
            response, reverse("users:mfa_verify"), fetch_redirect_response=False
        )
        self.assertTrue(MFADevice.objects.filter(user=self.user).exists())

    def test_disable_attempts_are_rate_limited(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["mfa_verified_user_id"] = str(self.user.pk)
        session.save()

        for _ in range(5):
            self.client.post(reverse("users:mfa_disable"), {"code": "000000"})
        response = self.client.post(
            reverse("users:mfa_disable"),
            {"code": TOTPService.generate_current_code(self.secret)},
        )

        self.assertRedirects(
            response, reverse("users:mfa_verify"), fetch_redirect_response=False
        )
        self.assertTrue(MFADevice.objects.filter(user=self.user).exists())

    def test_pending_login_for_a_deactivated_account_is_rejected(self):
        session = self.client.session
        session["pending_mfa_user_id"] = self.user.pk
        session["pending_mfa_expires_at"] = (
            timezone.now() + timedelta(minutes=5)
        ).timestamp()
        session.save()
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)

        response = self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(self.secret)},
        )

        self.assertRedirects(
            response, reverse("users:account_login"), fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_is_scoped_to_the_attacker_address(self):
        self.client.force_login(self.user)
        for _ in range(5):
            self.client.post(
                reverse("users:mfa_verify"),
                {"code": "000000"},
                REMOTE_ADDR="203.0.113.50",
            )

        owner = self.client.post(
            reverse("users:mfa_verify"),
            {"code": TOTPService.generate_current_code(self.secret)},
            REMOTE_ADDR="198.51.100.9",
        )

        self.assertRedirects(
            owner, reverse("users:dashboard"), fetch_redirect_response=False
        )


class NotificationRedirectTests(TestCase):
    def setUp(self):
        self.user = make_user("notify@example.com", "notify")
        self.notification = Notification.objects.create(
            recipient=self.user, title="Hello", message="Body"
        )
        self.client.force_login(self.user)

    def test_mark_read_never_redirects_to_a_foreign_referer(self):
        response = self.client.post(
            reverse("users:notification_mark_read", args=[self.notification.pk]),
            HTTP_REFERER="https://evil.example.net/phish",
        )

        self.assertRedirects(
            response, reverse("users:notifications"), fetch_redirect_response=False
        )

    def test_mark_all_read_keeps_a_same_site_referer(self):
        response = self.client.post(
            reverse("users:notification_mark_all"),
            HTTP_REFERER="http://testserver/users/dashboard/",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "http://testserver/users/dashboard/")


class PushAndPrivacyTests(TestCase):
    def setUp(self):
        self.user = make_user("push@example.com", "pushuser")
        self.client.force_login(self.user)

    def test_push_subscription_is_explicitly_unavailable_without_configuration(self):
        response = self.client.post(
            reverse("users:push_subscription"),
            data='{"endpoint": "https://push.example/x", "keys": {"p256dh": "a", "auth": "b"}}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("not enabled", response.json()["error"])

    def test_login_does_not_create_an_ip_bearing_notification(self):
        from django.contrib.auth.signals import user_logged_in

        request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.5")
        request.session = SessionStore()
        before = Notification.objects.filter(recipient=self.user).count()

        user_logged_in.send(sender=type(self.user), request=request, user=self.user)

        self.assertFalse(
            Notification.objects.filter(
                recipient=self.user, message__contains="203.0.113.5"
            ).exists()
        )
        self.assertGreaterEqual(
            Notification.objects.filter(recipient=self.user).count(), before
        )

    def test_public_display_name_never_exposes_the_email_address(self):
        named = get_user_model()(email="private@example.com", full_name=" Ada ")
        handle = get_user_model()(email="private2@example.com", username="ada_l")
        anonymous = get_user_model()(email="private3@example.com")

        self.assertEqual(named.public_display_name, "Ada")
        self.assertEqual(handle.public_display_name, "ada_l")
        self.assertNotIn("@", anonymous.public_display_name)
        self.assertNotIn("private3", anonymous.public_display_name)


class AdminVerificationActionTests(TestCase):
    def setUp(self):
        self.admin = CustomUserAdmin(get_user_model(), admin.site)
        self.target = make_user("target@example.com", "target", verified=False)
        EmailAddress.objects.create(
            user=self.target, email="secondary@example.com", verified=False
        )

    def request_for(self, user):
        request = RequestFactory().post("/admin/")
        request.user = user
        return request

    def test_only_superusers_can_mark_emails_verified(self):
        staff = make_user("staff-admin@example.com", "staffadmin", is_staff=True)

        with patch.object(self.admin, "message_user") as message_user:
            self.admin.mark_email_verified(
                self.request_for(staff),
                get_user_model().objects.filter(pk=self.target.pk),
            )

        self.target.refresh_from_db()
        self.assertIsNone(self.target.email_verified_at)
        self.assertIn("Only superusers", str(message_user.call_args.args[1]))

    def test_superuser_verification_touches_only_the_account_address_and_is_logged(
        self,
    ):
        from django.contrib.admin.models import LogEntry

        superuser = make_user(
            "super-admin@example.com",
            "superadmin",
            is_staff=True,
            is_superuser=True,
        )

        with patch.object(self.admin, "message_user"):
            self.admin.mark_email_verified(
                self.request_for(superuser),
                get_user_model().objects.filter(pk=self.target.pk),
            )

        self.target.refresh_from_db()
        self.assertIsNotNone(self.target.email_verified_at)
        primary = EmailAddress.objects.get(user=self.target, email=self.target.email)
        secondary = EmailAddress.objects.get(email="secondary@example.com")
        self.assertTrue(primary.verified)
        self.assertFalse(secondary.verified)
        self.assertFalse(secondary.primary)
        self.assertTrue(
            LogEntry.objects.filter(
                object_id=str(self.target.pk), user=superuser
            ).exists()
        )
