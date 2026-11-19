"""The profile-completion gate cannot be skipped by sending a header (audit F-028)."""

from __future__ import annotations

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

ONBOARDING = "/users/tell-us-about-you/"


@override_settings(REQUIRE_PROFILE_COMPLETION=True)
class ProfileGateHeaderTests(TestCase):
    def social_user(self, *, completed: bool = False):
        user = get_user_model().objects.create_user(
            email=f"gate-{completed}@example.com",
            username=f"gate{completed}",
            password="Strong-pass-4271!",
            profile_completed=completed,
        )
        SocialAccount.objects.create(user=user, provider="google", uid=f"g-{completed}")
        return user

    def setUp(self):
        self.client.force_login(self.social_user())

    def test_a_browser_page_is_redirected_with_its_destination(self):
        response = self.client.get(reverse("users:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(ONBOARDING))
        self.assertIn("next=", response["Location"])

    def test_htmx_requests_are_redirected_not_waved_through(self):
        response = self.client.get(reverse("users:dashboard"), HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], ONBOARDING)

    def test_ajax_requests_get_a_json_refusal(self):
        response = self.client.get(
            reverse("users:dashboard"), HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertTrue(body["profile_completion_required"])
        self.assertEqual(body["onboarding_url"], ONBOARDING)

    def test_a_json_content_type_does_not_skip_the_gate(self):
        response = self.client.post(
            reverse("users:push_subscription"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["profile_completion_required"])

    def test_a_header_never_reaches_the_gated_view(self):
        response = self.client.get(
            reverse("users:notification_settings"),
            HTTP_HX_REQUEST="true",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertNotEqual(response.status_code, 200)

    def test_onboarding_sign_out_and_mfa_pages_stay_reachable(self):
        for name in ("users:tell_us_about_you", "users:locale_hint"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name), HTTP_HX_REQUEST="true")
                self.assertEqual(response.status_code, 200)
        logout = self.client.post(reverse("users:account_logout"))
        self.assertEqual(logout.status_code, 302)
        self.assertFalse(logout["Location"].startswith(ONBOARDING))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_completed_profile_is_never_gated(self):
        self.client.force_login(self.social_user(completed=True))

        response = self.client.get(
            reverse("users:notification_settings"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(REQUIRE_PROFILE_COMPLETION=False)
    def test_the_gate_is_inactive_when_the_site_setting_is_off(self):
        response = self.client.get(
            reverse("users:notification_settings"), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(response.status_code, 200)
