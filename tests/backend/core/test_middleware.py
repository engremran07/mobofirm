from __future__ import annotations

from urllib.parse import urlencode

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


def make_user(email: str, username: str, **extra):
    user = get_user_model().objects.create_user(
        email=email, username=username, password="A-valid-password-983!", **extra
    )
    EmailAddress.objects.create(user=user, email=email, primary=True, verified=True)
    return user


class CorrelationIdTests(TestCase):
    def test_a_valid_inbound_id_is_reused_and_returned(self):
        response = self.client.get(
            reverse("health-live"), HTTP_X_REQUEST_ID="abc-12345"
        )

        self.assertEqual(response["X-Request-ID"], "abc-12345")

    def test_unsafe_inbound_ids_are_replaced(self):
        response = self.client.get(
            reverse("health-live"), HTTP_X_REQUEST_ID="x\nINJECTED log line"
        )

        self.assertNotIn("INJECTED", response["X-Request-ID"])
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f-]{36}$")

    def test_every_response_carries_an_id(self):
        self.assertTrue(self.client.get(reverse("health-live"))["X-Request-ID"])


@override_settings(MAINTENANCE_MODE=True)
class MaintenanceModeTests(TestCase):
    def test_visitors_get_a_503_with_retry_after(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "300")

    def test_health_probes_and_sign_in_stay_reachable(self):
        self.assertEqual(self.client.get(reverse("health-live")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("users:account_login")).status_code, 200
        )

    def test_staff_can_still_browse_the_site(self):
        staff = make_user("maint-staff@example.com", "maintstaff", is_staff=True)
        self.client.force_login(staff)

        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_disabled_by_default(self):
        with override_settings(MAINTENANCE_MODE=False):
            self.assertEqual(self.client.get(reverse("home")).status_code, 200)


class PasswordResetThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        make_user("reset-throttle@example.com", "resetthrottle")

    def post_reset(self, email="reset-throttle@example.com", **extra):
        return self.client.post("/accounts/password/reset/", {"email": email}, **extra)

    def test_repeated_requests_for_one_address_are_throttled(self):
        statuses = [self.post_reset().status_code for _ in range(4)]

        self.assertEqual(statuses[:3], [302, 302, 302])
        self.assertEqual(statuses[3], 429)

    def test_spoofed_forwarded_headers_do_not_reset_the_limit(self):
        for index in range(3):
            self.post_reset(HTTP_X_FORWARDED_FOR=f"9.9.9.{index}")

        response = self.post_reset(HTTP_X_FORWARDED_FOR="9.9.9.99")

        self.assertEqual(response.status_code, 429)


@override_settings(REQUIRE_PROFILE_COMPLETION=True)
class ProfileCompletionMiddlewareTests(TestCase):
    def social_user(self, completed: bool):
        user = make_user(
            f"social-{completed}@example.com",
            f"social{completed}",
            profile_completed=completed,
        )
        SocialAccount.objects.create(
            user=user, provider="google", uid=f"uid-{completed}"
        )
        return user

    def test_incomplete_social_users_are_sent_to_onboarding(self):
        self.client.force_login(self.social_user(False))

        response = self.client.get(reverse("users:dashboard"))

        # The page is reached again after onboarding, so the target travels along.
        onboarding = reverse("users:tell_us_about_you")
        query = urlencode({"next": reverse("users:dashboard")})
        self.assertRedirects(
            response, f"{onboarding}?{query}", fetch_redirect_response=False
        )

    def test_onboarding_page_and_health_are_exempt(self):
        self.client.force_login(self.social_user(False))

        self.assertEqual(
            self.client.get(reverse("users:tell_us_about_you")).status_code, 200
        )
        self.assertEqual(self.client.get(reverse("health-live")).status_code, 200)

    def test_completed_profiles_and_password_users_are_not_redirected(self):
        self.client.force_login(self.social_user(True))
        self.assertEqual(self.client.get(reverse("users:dashboard")).status_code, 200)

        password_user = make_user(
            "pw-user@example.com", "pwuser", profile_completed=False
        )
        self.client.force_login(password_user)
        self.assertNotEqual(
            self.client.get(reverse("users:dashboard")).get("Location"),
            reverse("users:tell_us_about_you"),
        )

    @override_settings(REQUIRE_PROFILE_COMPLETION=False)
    def test_inactive_when_the_setting_is_off(self):
        self.client.force_login(self.social_user(False))

        self.assertEqual(self.client.get(reverse("users:dashboard")).status_code, 200)
