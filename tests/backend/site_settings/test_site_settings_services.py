from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.site_settings import crypto
from apps.site_settings.models import (
    SECRET_NAMES,
    SiteSettings,
    VerificationFile,
    VerificationMetaTag,
)
from apps.site_settings.services import (
    SiteSettingsSnapshot,
    absolute_url,
    build_theme_css,
    clear_site_settings_cache,
    get_email_runtime_config,
    get_recaptcha_secret,
    get_site_settings,
    public_settings,
    site_origin,
)

OWNED_ELSEWHERE = {
    # users.UsersSettings
    "enable_signup",
    "require_mfa",
    "max_login_attempts",
    "rate_limit_window_seconds",
    # devices.DeviceConfig
    "max_devices",
    "fingerprint",
    # core.AppRegistry
    "pages_enabled",
    "shop_enabled",
    # seo.SEOSettings
    "sitemap_enabled",
    "robots_txt",
}


def stored(**fields: object) -> SiteSettings:
    row = SiteSettings.get_solo()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()
    return row


class OwnershipTests(TestCase):
    def test_site_settings_does_not_duplicate_other_apps_settings(self):
        names = {field.name for field in SiteSettings._meta.get_fields()}

        self.assertFalse(names & OWNED_ELSEWHERE)
        for forbidden in ("wallet", "credit", "bounty", "economy"):
            self.assertFalse([name for name in names if forbidden in name])

    def test_there_is_exactly_one_singleton_row(self):
        SiteSettings.get_solo()
        SiteSettings().save()

        self.assertEqual(SiteSettings.objects.count(), 1)


class SnapshotDefaultsTests(TestCase):
    @override_settings(SITE_NAME="Env Name", SITE_URL="https://env.example/")
    def test_blank_values_fall_back_to_the_environment(self):
        snapshot = get_site_settings()

        self.assertEqual(snapshot.site_name, "Env Name")
        self.assertEqual(snapshot.site_header, "Env Name")
        self.assertEqual(snapshot.site_url, "https://env.example")

    @override_settings(SITE_NAME="Env Name")
    def test_stored_values_win_over_the_environment(self):
        stored(site_name="Stored Name", site_header="Console")

        snapshot = get_site_settings()

        self.assertEqual(snapshot.site_name, "Stored Name")
        self.assertEqual(snapshot.site_header, "Console")

    def test_an_empty_database_is_the_pristine_environment_state(self):
        SiteSettings.objects.all().delete()
        clear_site_settings_cache()
        self.assertFalse(SiteSettings.objects.exists())

        snapshot = get_site_settings()

        self.assertIsInstance(snapshot, SiteSettingsSnapshot)
        self.assertFalse(snapshot.maintenance_mode)

    @override_settings(MAINTENANCE_MODE=True)
    def test_the_environment_can_force_maintenance_on(self):
        stored(maintenance_mode=False)

        self.assertTrue(get_site_settings().maintenance_mode)

    @override_settings(MAINTENANCE_MODE=False)
    def test_the_admin_flag_enables_maintenance(self):
        stored(maintenance_mode=True, maintenance_message="Back at noon")

        snapshot = get_site_settings()

        self.assertTrue(snapshot.maintenance_mode)
        self.assertEqual(snapshot.maintenance_message, "Back at noon")

    @override_settings(SITE_CONTACT_EMAIL="contact@mobofirm.invalid")
    def test_the_placeholder_contact_domain_is_never_public(self):
        self.assertEqual(get_site_settings().contact_email, "")
        stored(contact_email="help@example.org")

        self.assertEqual(get_site_settings().contact_email, "help@example.org")

    def test_social_links_keep_only_configured_networks(self):
        stored(social_github="https://github.com/mobofirm")

        links = get_site_settings().social_links

        self.assertEqual([link.key for link in links], ["github"])
        self.assertEqual(links[0].url, "https://github.com/mobofirm")

    @override_settings(LANGUAGE_CODE="en", TIME_ZONE="UTC")
    def test_localization_defaults_and_overrides(self):
        self.assertEqual(get_site_settings().default_language, "en")
        stored(default_language="ar", timezone="Africa/Cairo")

        snapshot = get_site_settings()

        self.assertEqual(snapshot.default_language, "ar")
        self.assertEqual(snapshot.timezone, "Africa/Cairo")


class DownloadPolicyTests(TestCase):
    @override_settings(
        FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS=7,
        FIRMWARE_DOWNLOAD_REQUIRE_LOGIN=True,
        FIRMWARE_DOWNLOAD_MAX_PER_DAY=3,
    )
    def test_null_inherits_the_environment(self):
        policy = get_site_settings().download

        self.assertEqual(policy.countdown_seconds, 7)
        self.assertTrue(policy.require_login)
        self.assertEqual(policy.max_per_day, 3)

    @override_settings(
        FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS=7, FIRMWARE_DOWNLOAD_REQUIRE_LOGIN=True
    )
    def test_stored_values_including_false_and_zero_are_deliberate_overrides(self):
        stored(download_countdown_seconds=0, download_require_login=False)

        policy = get_site_settings().download

        self.assertEqual(policy.countdown_seconds, 0)
        self.assertFalse(policy.require_login)

    def test_ranges_are_validated(self):
        row = SiteSettings.get_solo()
        row.download_ad_gate_seconds = 1
        row.download_token_expiry_minutes = 0

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("download_ad_gate_seconds", caught.exception.message_dict)
        self.assertIn("download_token_expiry_minutes", caught.exception.message_dict)


class RecaptchaTests(TestCase):
    @override_settings(
        RECAPTCHA_ENABLED=True, RECAPTCHA_MODE="v3", RECAPTCHA_SITE_KEY="env-site"
    )
    def test_environment_enables_recaptcha_when_the_row_inherits(self):
        config = get_site_settings().recaptcha

        self.assertTrue(config.enabled)
        self.assertEqual(config.mode, "v3")
        self.assertEqual(config.site_key, "env-site")

    @override_settings(
        RECAPTCHA_ENABLED=True, RECAPTCHA_MODE="v3", RECAPTCHA_SITE_KEY="env-site"
    )
    def test_the_admin_can_switch_it_off(self):
        stored(recaptcha_mode="off")

        config = get_site_settings().recaptcha

        self.assertFalse(config.enabled)
        self.assertEqual(config.mode, "off")

    @override_settings(RECAPTCHA_PRIVATE_KEY="env-secret")
    def test_the_stored_private_key_wins_and_the_environment_is_the_fallback(self):
        self.assertEqual(get_recaptcha_secret(), "env-secret")
        row = SiteSettings.get_solo()
        row.set_secret("recaptcha_private_key", "stored-secret")
        row.save()

        self.assertEqual(get_recaptcha_secret(), "stored-secret")

    def test_enabling_requires_both_keys(self):
        row = SiteSettings.get_solo()
        row.recaptcha_mode = SiteSettings.RecaptchaMode.V2

        with self.assertRaises(ValidationError) as caught:
            row.save()

        self.assertIn("recaptcha_site_key", caught.exception.message_dict)


class SecretTests(TestCase):
    def test_secrets_are_encrypted_at_rest_and_round_trip(self):
        row = SiteSettings.get_solo()
        row.set_secret("smtp_password", "s3cret-value")
        row.save()

        raw = SiteSettings.objects.values_list(
            "smtp_password_encrypted", flat=True
        ).get()

        self.assertNotIn("s3cret-value", raw)
        self.assertEqual(
            SiteSettings.get_solo().get_secret("smtp_password"), "s3cret-value"
        )

    def test_an_undecryptable_secret_is_an_explicit_error(self):
        row = SiteSettings.get_solo()
        row.smtp_password_encrypted = "not-a-fernet-token"
        row.save()

        with self.assertRaises(crypto.SecretUnavailableError):
            row.get_secret("smtp_password")

    def test_the_snapshot_never_carries_a_secret(self):
        row = SiteSettings.get_solo()
        for name in SECRET_NAMES:
            row.set_secret(name, f"plain-{name}")
        row.email_mode = SiteSettings.EmailMode.GMAIL
        row.gmail_username = "ops@example.org"
        row.save()

        snapshot = get_site_settings()
        rendered = repr(snapshot) + repr(public_settings(snapshot))

        for name in SECRET_NAMES:
            self.assertNotIn(f"plain-{name}", rendered)
        self.assertTrue(snapshot.email.credentials_configured)
        self.assertTrue(snapshot.recaptcha.secret_configured)

    def test_runtime_email_config_is_decrypted_on_demand(self):
        row = SiteSettings.get_solo()
        row.email_mode = SiteSettings.EmailMode.SMTP
        row.smtp_host = "smtp.example.org"
        row.smtp_username = "mailer"
        row.set_secret("smtp_password", "smtp-pass")
        row.save()

        config = get_email_runtime_config()

        self.assertEqual(config.mode, "smtp")
        self.assertEqual(config.smtp_password, "smtp-pass")
        self.assertEqual(config.smtp_host, "smtp.example.org")


class ValidationTests(TestCase):
    def test_a_light_accent_colour_is_rejected_for_contrast(self):
        row = SiteSettings.get_solo()
        row.primary_color = "#ffff00"

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("primary_color", caught.exception.message_dict)

    def test_colours_are_normalised_to_lower_case(self):
        row = stored(primary_color="#0B7376")

        self.assertEqual(row.primary_color, "#0b7376")

    def test_the_site_url_must_be_an_origin(self):
        row = SiteSettings.get_solo()
        row.site_url = "https://example.org/path?x=1"

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("site_url", caught.exception.message_dict)

    def test_gmail_mode_requires_address_and_app_password(self):
        row = SiteSettings.get_solo()
        row.email_mode = SiteSettings.EmailMode.GMAIL

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("gmail_username", caught.exception.message_dict)
        row.gmail_username = "ops@example.org"
        with self.assertRaises(ValidationError):
            row.full_clean()
        row.set_secret("gmail_app_password", "app-password")
        row.full_clean()

    def test_tls_and_ssl_are_mutually_exclusive(self):
        row = SiteSettings.get_solo()
        row.smtp_use_tls = True
        row.smtp_use_ssl = True

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("smtp_use_ssl", caught.exception.message_dict)

    def test_analytics_ids_are_format_checked(self):
        row = SiteSettings.get_solo()
        row.google_analytics_id = "UA-1"
        row.google_tag_manager_id = "not-gtm"

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("google_analytics_id", caught.exception.message_dict)
        self.assertIn("google_tag_manager_id", caught.exception.message_dict)

    def test_social_links_must_be_https(self):
        row = SiteSettings.get_solo()
        row.social_github = "http://github.com/x"

        with self.assertRaises(ValidationError) as caught:
            row.full_clean()

        self.assertIn("social_github", caught.exception.message_dict)

    def test_meta_tag_content_cannot_break_out_of_the_attribute(self):
        row = SiteSettings.get_solo()
        tag = VerificationMetaTag(
            site_settings=row,
            provider="Google",
            name="google-site-verification",
            content='abc"><script>alert(1)</script>',
        )

        with self.assertRaises(ValidationError) as caught:
            tag.full_clean()

        self.assertIn("content", caught.exception.message_dict)

    def test_verification_filenames_reject_paths_and_odd_extensions(self):
        row = SiteSettings.get_solo()
        for name in ("../secret.html", "a/b.html", "evil.php", ".htaccess"):
            with self.subTest(name=name):
                verification = VerificationFile(
                    site_settings=row, provider="Google", filename=name
                )
                with self.assertRaises(ValidationError):
                    verification.clean()


class CacheTests(TestCase):
    def test_the_snapshot_is_cached_and_invalidated_on_save(self):
        stored(site_name="First")
        get_site_settings()

        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(get_site_settings().site_name, "First")
        self.assertEqual(len(queries), 0)

        stored(site_name="Second")
        self.assertEqual(get_site_settings().site_name, "Second")

    def test_meta_tags_and_files_invalidate_the_snapshot(self):
        row = SiteSettings.get_solo()
        self.assertEqual(get_site_settings().meta_tags, ())

        tag = VerificationMetaTag.objects.create(
            site_settings=row,
            provider="Google",
            name="google-site-verification",
            content="tok",
        )
        self.assertEqual(
            get_site_settings().meta_tags, (("google-site-verification", "tok"),)
        )

        tag.delete()
        self.assertEqual(get_site_settings().meta_tags, ())

    def test_inactive_meta_tags_are_not_published(self):
        VerificationMetaTag.objects.create(
            site_settings=SiteSettings.get_solo(),
            provider="Bing",
            name="msvalidate.01",
            content="tok",
            is_active=False,
        )

        self.assertEqual(get_site_settings().meta_tags, ())

    def test_changing_a_watched_django_setting_refreshes_the_snapshot(self):
        before = get_site_settings().site_name
        with override_settings(SITE_NAME=f"{before} X"):
            self.assertEqual(get_site_settings().site_name, f"{before} X")
        self.assertEqual(get_site_settings().site_name, before)

    def test_clearing_the_cache_rotates_the_version(self):
        first = get_site_settings().version

        clear_site_settings_cache()

        self.assertNotEqual(get_site_settings().version, first)


class UrlTests(TestCase):
    @override_settings(SITE_URL="")
    def test_origin_falls_back_to_the_request_and_honours_force_https(self):
        request = RequestFactory().get("/", HTTP_HOST="testserver")

        self.assertEqual(site_origin(request), "http://testserver")
        stored(force_https=True)
        self.assertEqual(site_origin(request), "https://testserver")
        self.assertEqual(site_origin(), "")

    @override_settings(SITE_URL="https://example.org")
    def test_the_configured_url_wins_and_only_site_paths_are_absolutised(self):
        request = RequestFactory().get("/", HTTP_HOST="evil.test")

        self.assertEqual(absolute_url("/a/", request), "https://example.org/a/")
        self.assertEqual(absolute_url("//evil.test/x", request), "")
        self.assertEqual(absolute_url("relative", request), "")
        self.assertEqual(
            absolute_url("https://cdn.example.org/i.png", request),
            "https://cdn.example.org/i.png",
        )


class ThemeCssTests(TestCase):
    def test_no_customisation_produces_no_css(self):
        self.assertEqual(build_theme_css(get_site_settings()), "")

    def test_accent_colours_override_only_the_light_theme(self):
        stored(primary_color="#0b7376", secondary_color="#6b2fa0")

        css = build_theme_css(get_site_settings())

        self.assertIn(':root[data-bs-theme="light"]', css)
        self.assertIn("--mf-accent: #0b7376;", css)
        self.assertIn("--mf-accent-cool: #6b2fa0;", css)
        self.assertNotIn('data-bs-theme="dark"]', css)
