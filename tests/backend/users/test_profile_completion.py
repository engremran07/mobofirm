from __future__ import annotations

from allauth.account.models import EmailAddress
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.users.models import CustomUser
from apps.users.profile_models import Profile
from apps.users.services.location_hints import hint_from_browser, hint_from_request
from apps.users.services.profile_completion import (
    is_profile_complete,
    missing_profile_fields,
    profile_completion_url,
    safe_next_url,
)
from apps.wallet import api as wallet_api
from apps.wallet.models import LedgerEntry


def _verified_user(**extra: object) -> CustomUser:
    user = CustomUser.objects.create_user(
        username=str(extra.pop("username", "newcomer")),
        email="newcomer@example.com",
        password="Pass-12345-word",
        email_verified_at=timezone.now(),
        **extra,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, primary=True, verified=True
    )
    return user


class ProfileCompletionRulesTests(TestCase):
    def test_incomplete_profile_lists_each_missing_field(self):
        user = _verified_user()

        self.assertFalse(is_profile_complete(user))
        self.assertEqual(
            missing_profile_fields(user), ["full_name", "country", "interests"]
        )

    def test_complete_profile_needs_confirmation_flag(self):
        user = _verified_user(full_name="New Comer", country="PK")
        Profile.objects.create(
            user=user, social_links={"interests": ["firmware_download"]}
        )

        self.assertEqual(missing_profile_fields(user), ["profile_completed"])
        self.assertFalse(is_profile_complete(user))
        user.profile_completed = True
        user.save(update_fields=["profile_completed"])
        self.assertTrue(is_profile_complete(user))

    def test_email_as_username_counts_as_missing(self):
        user = _verified_user(username="newcomer@example.com")

        self.assertIn("username", missing_profile_fields(user))

    def test_safe_next_url_accepts_only_same_site_paths(self):
        request = RequestFactory().get("/", HTTP_HOST="testserver")

        self.assertEqual(
            safe_next_url(request, "/marketplace/seller/"), "/marketplace/seller/"
        )
        for hostile in (
            "https://evil.example/",
            "//evil.example/",
            "/\\evil.example",
            "javascript:alert(1)",
            "/ok\r\nSet-Cookie: a=b",
            "",
        ):
            self.assertEqual(safe_next_url(request, hostile), "", hostile)

    def test_profile_completion_url_carries_next(self):
        self.assertEqual(profile_completion_url(), reverse("users:tell_us_about_you"))
        self.assertEqual(
            profile_completion_url("/x/?a=1"),
            f"{reverse('users:tell_us_about_you')}?next=%2Fx%2F%3Fa%3D1",
        )


class LocationHintTests(TestCase):
    def test_timezone_wins_over_language(self):
        hint = hint_from_browser("Asia/Karachi", "en-US")

        assert hint is not None
        self.assertEqual((hint.country, hint.source), ("PK", "timezone"))
        self.assertEqual(hint.calling_code, "+92")

    def test_language_region_is_a_weak_hint(self):
        hint = hint_from_browser("Mars/Olympus", "en-GB")

        assert hint is not None
        self.assertEqual((hint.country, hint.source), ("GB", "language"))
        self.assertTrue(hint.is_weak)

    def test_unknown_signals_give_no_hint(self):
        self.assertIsNone(hint_from_browser("Nowhere/Zone", "xx"))
        self.assertIsNone(hint_from_browser(None, None))

    @override_settings(COUNTRY_HEADER="CF-IPCountry", TRUSTED_PROXY_COUNT=1)
    def test_trusted_edge_header_beats_accept_language(self):
        request = RequestFactory().get(
            "/", HTTP_CF_IPCOUNTRY="pk", HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9"
        )

        hint = hint_from_request(request)

        assert hint is not None
        self.assertEqual((hint.country, hint.source), ("PK", "edge"))

    @override_settings(COUNTRY_HEADER="CF-IPCountry", TRUSTED_PROXY_COUNT=0)
    def test_untrusted_edge_header_is_ignored_for_accept_language(self):
        request = RequestFactory().get(
            "/", HTTP_CF_IPCOUNTRY="PK", HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9"
        )

        hint = hint_from_request(request)

        assert hint is not None
        self.assertEqual((hint.country, hint.source), ("DE", "accept-language"))


class LocaleHintEndpointTests(TestCase):
    def test_returns_country_and_calling_code_for_a_zone(self):
        response = self.client.get(
            reverse("users:locale_hint"), {"tz": "Asia/Karachi", "lang": "en-US"}
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["detected"])
        self.assertEqual(data["country"], "PK")
        self.assertEqual(data["calling_code"], "+92")
        self.assertIn("Pakistan", data["country_label"])

    def test_unknown_values_answer_not_detected(self):
        response = self.client.get(reverse("users:locale_hint"), {"tz": "", "lang": ""})

        self.assertEqual(response.json(), {"detected": False})

    def test_post_is_not_allowed(self):
        self.assertEqual(
            self.client.post(reverse("users:locale_hint")).status_code, 405
        )


class ProfileCompletionPageTests(TestCase):
    def setUp(self):
        self.user = _verified_user()
        self.client.force_login(self.user)
        self.url = reverse("users:tell_us_about_you")

    def test_page_renders_comboboxes_and_breadcrumbs(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-mode="pick"', count=2)
        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, 'x-data="profileLocale"')
        self.assertContains(response, reverse("users:locale_hint"))

    def test_page_prefills_from_accept_language_without_saving(self):
        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9")

        self.assertContains(response, "Detected: ")
        self.assertEqual(response.context["form"].initial["country"], "DE")
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, "")

    def test_saved_country_is_not_overridden_by_hints(self):
        self.user.country = "PK"
        self.user.save(update_fields=["country"])

        response = self.client.get(self.url, HTTP_ACCEPT_LANGUAGE="de-DE")

        self.assertEqual(response.context["form"].initial["country"], "PK")
        self.assertNotContains(response, "Detected: ")

    def test_post_saves_profile_and_follows_safe_next(self):
        response = self.client.post(
            self.url,
            {
                "username": "newcomer",
                "full_name": "New Comer",
                "country": "PK",
                "phone_country_code": "+92",
                "phone": "3001234567",
                "interests": ["firmware_download"],
                "next": "/marketplace/",
            },
        )

        self.assertRedirects(response, "/marketplace/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.country, "PK")
        self.assertEqual(self.user.phone_country_code, "+92")
        self.assertTrue(self.user.profile_completed)
        self.assertTrue(is_profile_complete(self.user))

    def test_completing_the_profile_pays_the_wallet_reward_once(self):
        payload = {
            "username": "newcomer",
            "full_name": "New Comer",
            "country": "PK",
            "interests": ["firmware_download"],
        }

        self.client.post(self.url, payload)
        self.client.post(self.url, payload)

        entries = LedgerEntry.objects.filter(key=f"profile:{self.user.pk}")
        self.assertEqual(entries.count(), 1)
        self.assertEqual(wallet_api.balance(self.user), entries.get().amount)
        self.assertGreater(wallet_api.balance(self.user), 0)

    def test_incomplete_submission_pays_nothing(self):
        response = self.client.post(self.url, {"username": "newcomer"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(LedgerEntry.objects.filter(wallet__user=self.user).exists())

    def test_post_ignores_foreign_next(self):
        response = self.client.post(
            self.url,
            {
                "username": "newcomer",
                "full_name": "New Comer",
                "country": "PK",
                "interests": ["firmware_download"],
                "next": "https://evil.example/",
            },
        )

        self.assertRedirects(
            response, reverse("users:dashboard"), fetch_redirect_response=False
        )

    def test_unknown_country_is_rejected_server_side(self):
        response = self.client.post(
            self.url,
            {
                "username": "newcomer",
                "full_name": "New Comer",
                "country": "ZZ",
                "interests": ["firmware_download"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("country"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.profile_completed)

    def test_next_is_kept_in_the_form_when_valid(self):
        response = self.client.get(self.url, {"next": "/marketplace/"})

        self.assertContains(response, 'name="next" value="/marketplace/"')

    def test_anonymous_users_are_sent_to_login(self):
        self.client.logout()

        self.assertEqual(self.client.get(self.url).status_code, 302)


class UserAreaBreadcrumbTests(TestCase):
    def setUp(self):
        self.user = _verified_user(full_name="New Comer", country="PK")
        Profile.objects.create(
            user=self.user, social_links={"interests": ["firmware_download"]}
        )
        self.user.profile_completed = True
        self.user.save(update_fields=["profile_completed"])
        self.client.force_login(self.user)

    def test_every_account_page_has_breadcrumbs(self):
        names = (
            "users:dashboard",
            "users:profile",
            "users:tell_us_about_you",
            "users:mfa_setup",
            "users:notification_settings",
            "users:notifications",
            "devices:my_devices",
            "library:my_files",
            "library:my_uploads",
            "library:my_quota",
        )
        for name in names:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'aria-label="Breadcrumb"')
