from __future__ import annotations

import tempfile
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.pages.models import Page
from apps.site_settings.models import (
    SiteSettings,
    VerificationFile,
    VerificationMetaTag,
)

GOOGLE_NAME = "google1234abcd.html"
GOOGLE_BODY = b"google-site-verification: google1234abcd.html"


def stored(**fields: Any) -> SiteSettings:
    row = SiteSettings.get_solo()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


class MediaTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        override = override_settings(MEDIA_ROOT=self._media.name)
        override.enable()
        self.addCleanup(override.disable)

    def make_file(
        self,
        name: str = GOOGLE_NAME,
        body: bytes = GOOGLE_BODY,
        **extra: Any,
    ) -> VerificationFile:
        return VerificationFile.objects.create(
            site_settings=SiteSettings.get_solo(),
            provider="Google",
            file=SimpleUploadedFile(name, body),
            **extra,
        )


class VerificationFileTests(MediaTestCase):
    def read(self, response: Any) -> bytes:
        return b"".join(response.streaming_content)

    def test_an_active_file_is_served_under_site_verification(self):
        self.make_file()

        response = self.client.get(
            reverse("site_settings:verification_file", args=[GOOGLE_NAME])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.read(response), GOOGLE_BODY)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(
            response["Content-Security-Policy"], "default-src 'none'; sandbox"
        )

    def test_html_files_are_also_answered_at_the_site_root(self):
        self.make_file()

        response = self.client.get(f"/{GOOGLE_NAME}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.read(response), GOOGLE_BODY)

    def test_text_files_stay_under_the_site_prefix(self):
        self.make_file("BingSiteAuth.txt", b"token")

        self.assertEqual(self.client.get("/BingSiteAuth.txt").status_code, 404)
        response = self.client.get(
            reverse("site_settings:verification_file", args=["BingSiteAuth.txt"])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        response.close()

    def test_unknown_inactive_and_unsafe_names_are_404(self):
        self.make_file(is_active=False)
        self.make_file("BingSiteAuth.xml", b"<a/>")
        for name in (
            GOOGLE_NAME,
            "missing.html",
            "evil.php",
            "..%2Fsecret.html",
            ".htaccess",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    self.client.get(f"/site/verification/{name}/").status_code, 404
                )
                self.assertEqual(self.client.get(f"/{name}").status_code, 404)

    def test_a_deactivated_file_disappears_immediately(self):
        verification = self.make_file()
        url = reverse("site_settings:verification_file", args=[GOOGLE_NAME])
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        first.close()

        verification.is_active = False
        verification.save()

        self.assertEqual(self.client.get(url).status_code, 404)

    def test_other_root_routes_are_not_shadowed(self):
        self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)

    def test_only_safe_methods_are_allowed(self):
        self.make_file()

        response = self.client.post(
            reverse("site_settings:verification_file", args=[GOOGLE_NAME])
        )

        self.assertEqual(response.status_code, 405)

    def test_the_filename_defaults_to_the_upload_name_and_is_unique(self):
        first = self.make_file()
        self.assertEqual(first.filename, GOOGLE_NAME)

        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.make_file()

    def test_oversized_uploads_are_rejected(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.make_file("big.txt", b"x" * (256 * 1024 + 1))


class PublicEndpointTests(TestCase):
    def test_site_info_is_public_json_without_secrets(self):
        row = SiteSettings.get_solo()
        row.set_secret("recaptcha_private_key", "very-secret-key")
        row.set_secret("smtp_password", "mail-secret")
        row.site_name = "Info Site"
        row.save()

        response = self.client.get("/site/info/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["site_name"], "Info Site")
        self.assertIn("recaptcha", payload)
        body = response.content.decode()
        self.assertNotIn("very-secret-key", body)
        self.assertNotIn("mail-secret", body)
        self.assertNotIn("password", body.lower())
        self.assertEqual(self.client.post("/site/info/").status_code, 405)

    def test_theme_css_reflects_the_stored_colours(self):
        stored(primary_color="#0b7376")

        response = self.client.get(reverse("site_settings:theme_css"))

        self.assertEqual(response["Content-Type"], "text/css; charset=utf-8")
        self.assertIn("#0b7376", response.content.decode())

    def test_legacy_legal_paths_redirect_to_published_cms_pages(self):
        Page.objects.create(
            slug="privacy", title="Privacy", content="x", status=Page.Status.PUBLISHED
        )
        Page.objects.create(
            slug="consent", title="Consent", content="x", status=Page.Status.PUBLISHED
        )

        privacy = self.client.get("/privacy/")
        cookies = self.client.get("/cookies/")

        self.assertEqual(privacy.status_code, 301)
        self.assertEqual(
            privacy["Location"], reverse("pages:page", kwargs={"slug": "privacy"})
        )
        self.assertEqual(cookies.status_code, 301)
        self.assertEqual(
            cookies["Location"], reverse("pages:page", kwargs={"slug": "consent"})
        )

    def test_legacy_paths_without_a_published_page_are_404(self):
        Page.objects.create(
            slug="terms", title="Terms", content="x", status=Page.Status.DRAFT
        )

        for path in ("/privacy/", "/terms/", "/cookies/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class PageContextTests(TestCase):
    def test_the_context_processor_exposes_the_typed_snapshot(self):
        stored(site_name="Context Site", social_github="https://github.com/mobofirm")

        response = self.client.get("/")

        snapshot = response.context["site_settings"]
        self.assertEqual(snapshot.site_name, "Context Site")
        self.assertEqual(response.context["site_name"], "Context Site")
        self.assertContains(response, "Context Site")
        self.assertContains(response, "https://github.com/mobofirm")

    def test_the_head_carries_verification_meta_tags_and_favicon(self):
        row = SiteSettings.get_solo()
        VerificationMetaTag.objects.create(
            site_settings=row,
            provider="Google",
            name="google-site-verification",
            content="tok-123",
        )
        SiteSettings.objects.update(favicon="branding/fav.png")
        from apps.site_settings.services import clear_site_settings_cache

        clear_site_settings_cache()

        response = self.client.get("/")

        self.assertContains(
            response, '<meta name="google-site-verification" content="tok-123">'
        )
        self.assertContains(response, 'rel="icon" href="/media/branding/fav.png"')

    def test_analytics_loads_only_through_the_same_origin_script(self):
        stored(google_analytics_id="G-TEST12345")

        response = self.client.get("/")
        html = response.content.decode()

        self.assertIn("site_settings/analytics.js", html)
        self.assertIn('data-ga4-id="G-TEST12345"', html)
        self.assertNotIn("googletagmanager.com/gtag", html)

    def test_no_analytics_markup_without_an_id(self):
        html = self.client.get("/").content.decode()

        self.assertNotIn("site_settings/analytics.js", html)

    def test_the_theme_stylesheet_is_linked_only_when_customised(self):
        self.assertNotIn(
            reverse("site_settings:theme_css"), self.client.get("/").content.decode()
        )
        stored(primary_color="#0b7376")

        self.assertIn(
            reverse("site_settings:theme_css"), self.client.get("/").content.decode()
        )


class AnalyticsCspTests(TestCase):
    @staticmethod
    def policy(response: Any) -> str:
        return str(response.headers.get("Content-Security-Policy", ""))

    def test_hosts_are_added_only_when_an_id_is_configured(self):
        self.assertNotIn("googletagmanager", self.policy(self.client.get("/")))

        stored(google_tag_manager_id="GTM-ABC123")
        policy = self.policy(self.client.get("/"))

        self.assertIn("https://www.googletagmanager.com", policy)
        self.assertIn("script-src", policy)
        self.assertNotIn("'unsafe-inline'", policy.split("script-src")[1].split(";")[0])

    def test_non_html_and_admin_responses_are_untouched(self):
        stored(google_analytics_id="G-TEST12345")

        self.assertNotIn(
            "googletagmanager", self.policy(self.client.get("/site/info/"))
        )
        self.assertNotIn(
            "googletagmanager", self.policy(self.client.get(reverse("admin:login")))
        )

    def test_removing_the_id_removes_the_hosts(self):
        stored(google_analytics_id="G-TEST12345")
        self.assertIn("googletagmanager", self.policy(self.client.get("/")))

        stored(google_analytics_id="")

        self.assertNotIn("googletagmanager", self.policy(self.client.get("/")))


class MaintenanceTests(TestCase):
    def test_the_admin_flag_serves_503_with_the_message(self):
        stored(maintenance_mode=True, maintenance_message="Back at noon")

        response = self.client.get("/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "300")
        self.assertContains(response, "Back at noon", status_code=503)

    def test_operational_paths_and_staff_stay_reachable(self):
        stored(maintenance_mode=True)
        staff = get_user_model().objects.create_superuser(
            username="ops", email="ops@example.org", password="Pa55-word-for-tests"
        )

        for path in ("/health/live/", "/site/info/", reverse("admin:login")):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 503)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_turning_it_off_restores_the_site(self):
        stored(maintenance_mode=True)
        self.assertEqual(self.client.get("/").status_code, 503)

        stored(maintenance_mode=False)

        self.assertEqual(self.client.get("/").status_code, 200)


class LocaleTests(TestCase):
    def test_the_default_language_applies_without_a_browser_preference(self):
        stored(default_language="ar")

        response = self.client.get("/")

        self.assertEqual(response["Content-Language"], "ar")

    def test_an_explicit_accept_language_wins(self):
        stored(default_language="ar")

        response = self.client.get("/", headers={"Accept-Language": "en"})

        self.assertEqual(response["Content-Language"], "en")
