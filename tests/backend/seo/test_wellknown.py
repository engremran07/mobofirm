from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ads.models import AdsSettings
from apps.seo.models import SEOSettings
from tests.backend.seo.seo_helpers import SITE


@override_settings(SITE_URL=SITE)
class SecurityTxtTests(TestCase):
    def configure(self, **fields):
        row = SEOSettings.get_solo()
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        return row

    def test_not_served_until_configured(self):
        self.assertEqual(self.client.get("/.well-known/security.txt").status_code, 404)

    def test_rfc_9116_fields_for_an_email_contact(self):
        expires = timezone.now() + timedelta(days=200)
        self.configure(
            security_contact="security@example.org",
            security_policy_url="https://example.org/security-policy/",
            security_expires=expires,
        )

        response = self.client.get("/.well-known/security.txt")
        lines = response.content.decode().splitlines()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(lines[0], "Contact: mailto:security@example.org")
        self.assertRegex(lines[1], r"^Expires: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIn("Policy: https://example.org/security-policy/", lines)
        self.assertIn(f"Canonical: {SITE}/.well-known/security.txt", lines)

    def test_https_contact_is_used_as_is_and_an_expired_file_is_withdrawn(self):
        self.configure(
            security_contact="https://example.org/report/",
            security_expires=timezone.now() + timedelta(days=5),
        )
        self.assertContains(
            self.client.get("/.well-known/security.txt"),
            "Contact: https://example.org/report/",
        )

        self.configure(security_expires=timezone.now() - timedelta(minutes=1))

        self.assertEqual(self.client.get("/.well-known/security.txt").status_code, 404)

    def test_settings_validation_rejects_incomplete_or_unsafe_values(self):
        row = SEOSettings.get_solo()
        row.security_contact = "security@example.org"
        with self.assertRaises(ValidationError) as missing:
            row.full_clean()
        self.assertIn("security_expires", missing.exception.message_dict)

        row.security_expires = timezone.now() + timedelta(days=5)
        row.security_contact = "javascript:alert(1)"
        with self.assertRaises(ValidationError) as unsafe:
            row.full_clean()
        self.assertIn("security_contact", unsafe.exception.message_dict)


@override_settings(SITE_URL=SITE)
class OptionalTextFileTests(TestCase):
    def test_humans_txt_is_off_by_default_and_served_when_enabled(self):
        self.assertEqual(self.client.get("/humans.txt").status_code, 404)

        row = SEOSettings.get_solo()
        row.humans_txt_enabled = True
        row.humans_txt = "Team: the Mobofirm crew"
        row.save()

        response = self.client.get("/humans.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "Team: the Mobofirm crew\n")

    def test_enabled_humans_txt_with_no_body_is_not_served(self):
        row = SEOSettings.get_solo()
        row.humans_txt_enabled = True
        row.save()

        self.assertEqual(self.client.get("/humans.txt").status_code, 404)

    def test_llms_txt_is_deliberately_not_served(self):
        self.assertEqual(self.client.get("/llms.txt").status_code, 404)

    def test_the_indexnow_key_route_still_works_beside_the_named_files(self):
        row = SEOSettings.get_solo()
        row.indexnow_enabled = True
        row.indexnow_key = "abc123def4567890"
        row.save()

        self.assertEqual(self.client.get("/abc123def4567890.txt").status_code, 200)
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)


@override_settings(SITE_URL=SITE)
class AdsTxtTests(TestCase):
    def configure(self, **fields):
        row = AdsSettings.get_solo()
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()

    def test_not_served_without_a_configured_publisher_id(self):
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)
        self.configure(ads_enabled=True, adsense_enabled=True, adsense_publisher_id="")
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)
        self.configure(adsense_publisher_id="not-an-id")
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)

    def test_adsense_line_comes_from_the_ads_settings_only_when_enabled(self):
        self.configure(
            ads_enabled=True,
            adsense_enabled=True,
            adsense_publisher_id="ca-pub-1234567890123456",
        )

        response = self.client.get("/ads.txt")

        self.assertEqual(
            response.content.decode(),
            "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n",
        )
        self.configure(adsense_enabled=False)
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)

    def test_app_ads_txt_has_no_provider_and_is_not_served(self):
        self.assertEqual(self.client.get("/app-ads.txt").status_code, 404)
