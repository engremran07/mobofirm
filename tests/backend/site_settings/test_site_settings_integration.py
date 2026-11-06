from __future__ import annotations

from django.test import TestCase, override_settings

from apps.seo.inventory import resolve_path
from apps.site_settings.models import SiteSettings
from apps.site_settings.services import clear_site_settings_cache, get_site_settings
from apps.users.services.email_verification import generate_verification_code


class SeoDefaultsTests(TestCase):
    @override_settings(SITE_URL="https://example.org")
    def test_share_image_and_twitter_handle_come_from_the_site_settings(self):
        row = SiteSettings.get_solo()
        row.twitter_handle = "mobofirm"
        row.save()
        SiteSettings.objects.update(og_image="branding/share.png")
        clear_site_settings_cache()

        resolved = resolve_path("/")

        assert resolved is not None
        self.assertEqual(resolved.twitter["twitter:site"], "@mobofirm")
        self.assertEqual(
            resolved.og["og:image"], "https://example.org/media/branding/share.png"
        )
        self.assertEqual(resolved.og["og:site_name"], get_site_settings().site_name)

    @override_settings(SITE_NAME="Env Brand", SITE_URL="https://example.org")
    def test_the_site_name_follows_the_runtime_value(self):
        row = SiteSettings.get_solo()
        row.site_name = "Runtime Brand"
        row.save()

        resolved = resolve_path("/")

        assert resolved is not None
        self.assertEqual(resolved.og["og:site_name"], "Runtime Brand")


class UsersIntegrationTests(TestCase):
    def test_verification_code_shape_follows_the_settings(self):
        row = SiteSettings.get_solo()
        row.email_verification_code_length = 8
        row.email_verification_code_type = SiteSettings.CodeType.NUMERIC
        row.save()

        code = generate_verification_code()

        self.assertEqual(len(code), 8)
        self.assertTrue(code.isdigit())

    @override_settings(REQUIRE_PROFILE_COMPLETION=True)
    def test_profile_completion_inherits_then_can_be_disabled(self):
        self.assertTrue(get_site_settings().require_profile_completion)
        row = SiteSettings.get_solo()
        row.require_profile_completion = False
        row.save()

        self.assertFalse(get_site_settings().require_profile_completion)


class FirmwareDownloadPolicyTests(TestCase):
    def test_the_download_service_reads_the_accessor(self):
        from apps.firmwares import download_service

        row = SiteSettings.get_solo()
        row.download_countdown_seconds = 42
        row.save()

        self.assertEqual(get_site_settings().download.countdown_seconds, 42)
        self.assertIn("get_site_settings", vars(download_service))


class SingleAuthorityTests(TestCase):
    def test_no_other_app_defines_a_second_site_settings_model(self):
        from django.apps import apps

        names = [
            f"{model._meta.app_label}.{model.__name__}"
            for model in apps.get_models()
            if model.__name__ in {"SiteSettings", "SiteConfig", "GlobalSettings"}
        ]

        self.assertEqual(names, ["site_settings.SiteSettings"])

    def test_the_duplicate_app_registry_is_not_in_site_settings(self):
        from django.apps import apps

        models = {m.__name__ for m in apps.get_app_config("site_settings").get_models()}

        self.assertEqual(
            models, {"SiteSettings", "VerificationMetaTag", "VerificationFile"}
        )
