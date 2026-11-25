from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.ads.admin import AdsSettingsForm
from apps.ads.models import (
    AdEvent,
    AdNetwork,
    AdPlacement,
    AdsSettings,
    AffiliateClick,
    AffiliateProvider,
    Campaign,
    RewardedAdView,
)
from apps.ads.services.analytics.tracker import record_event
from apps.ads.templatetags.ad_tags import _safe_click_url, _validate_ad_creative_code
from apps.users.services.consent import hash_ip, hash_ua


class AdCreativeValidatorTests(SimpleTestCase):
    def test_allowlisted_https_script_is_accepted_with_any_attribute_spacing(self):
        self.assertTrue(
            _validate_ad_creative_code(
                '<script async src = "https://pagead2.googlesyndication.com/a.js">'
                "</script>"
            )
        )

    def test_bypass_attempts_are_rejected(self):
        attempts = {
            "spaced src to a foreign host": '<script src = "https://evil.example/x.js"></script>',
            "protocol relative": '<script src="//pagead2.googlesyndication.com/a.js"></script>',
            "plain http": '<script src="http://pagead2.googlesyndication.com/a.js"></script>',
            "lookalike host": '<script src="https://pagead2.googlesyndication.com.evil.example/a.js"></script>',
            "inline body": '<script src="https://pagead2.googlesyndication.com/a.js">alert(1)</script>',
            "no src": "<script>alert(1)</script>",
            "javascript url": '<a href="java\tscript:alert(1)">x</a>',
            "event handler": '<img src="https://x.example/a.png" onerror="alert(1)">',
            "style attribute": '<div style="position:fixed">x</div>',
            "data url": '<a href="data:text/html;base64,AAAA">x</a>',
            "srcdoc": '<iframe srcdoc="<script>1</script>"></iframe>',
        }
        for label, markup in attempts.items():
            with self.subTest(label):
                self.assertFalse(_validate_ad_creative_code(markup))

    def test_plain_banner_markup_is_accepted(self):
        self.assertTrue(
            _validate_ad_creative_code(
                '<a href="https://shop.example/offer"><img src="https://cdn.example/b.png" alt="Offer"></a>'
            )
        )

    def test_click_urls_must_be_absolute_http_or_https(self):
        self.assertEqual(
            _safe_click_url("https://shop.example/x"), "https://shop.example/x"
        )
        self.assertEqual(_safe_click_url("javascript:alert(1)"), "")
        self.assertEqual(_safe_click_url("//evil.example/x"), "")
        self.assertEqual(_safe_click_url(""), "")


class CspAdHostTests(SimpleTestCase):
    def test_no_third_party_script_hosts_by_default(self):
        from app.settings.security import BASE_CSP

        self.assertEqual(BASE_CSP["script-src"], ["'self'"])
        self.assertEqual(BASE_CSP["frame-src"], ["'none'"])

    def test_operator_configured_hosts_are_validated_and_added(self):
        import os
        import subprocess
        import sys

        env = {**os.environ, "CSP_AD_HOSTS": "ads.example.com, bad host/x ,cdn.ads.net"}
        code = (
            "from app.settings.security import BASE_CSP as c;"
            "print(c['script-src']);print(c['frame-src'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

        self.assertIn("https://ads.example.com", result.stdout)
        self.assertIn("https://cdn.ads.net", result.stdout)
        self.assertNotIn("bad host", result.stdout)


class ToggleSettingsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            email="ads-staff@example.com",
            username="adsstaff",
            password="x-Valid-pass-1!",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        settings_obj = AdsSettings.get_solo()
        settings_obj.ads_enabled = True
        settings_obj.save()

    def test_unknown_actions_do_not_switch_ads_off(self):
        response = self.client.post(reverse("ads:toggle_settings"), {"action": "bogus"})

        self.assertEqual(response.status_code, 400)
        self.assertTrue(AdsSettings.get_solo().ads_enabled)

    def test_placement_action_without_a_target_does_not_switch_ads_off(self):
        response = self.client.post(
            reverse("ads:toggle_settings"), {"action": "disable_placement"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(AdsSettings.get_solo().ads_enabled)

    def test_the_global_settings_form_still_works(self):
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_adssettings")
        )
        response = self.client.post(
            reverse("ads:toggle_settings"), {"ads_enabled": "0"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AdsSettings.get_solo().ads_enabled)

    def test_ads_settings_form_keeps_fields_explicit_and_secret_write_only(self):
        form = AdsSettingsForm()

        self.assertIn("ads_enabled", form.fields)
        self.assertIn("custom_config", form.fields)
        self.assertIn("new_amazon_paapi_secret_key", form.fields)
        self.assertNotIn("amazon_paapi_secret_key", form.fields)

    def test_staff_without_settings_permission_cannot_change_global_settings(self):
        response = self.client.post(
            reverse("ads:toggle_settings"), {"ads_enabled": "0"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(AdsSettings.get_solo().ads_enabled)

    def test_settings_permission_cannot_toggle_a_placement(self):
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_adssettings")
        )
        placement = AdPlacement.objects.create(
            name="Permission-protected placement",
            code="permission-protected-placement",
            slug="permission-protected-placement",
        )

        response = self.client.post(
            reverse("ads:toggle_settings"),
            {"action": "disable_placement", "placement": placement.pk},
        )

        self.assertEqual(response.status_code, 403)
        placement.refresh_from_db()
        self.assertTrue(placement.is_enabled)

    def test_placement_permission_can_toggle_a_placement(self):
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_adplacement")
        )
        placement = AdPlacement.objects.create(
            name="Authorized placement",
            code="authorized-placement",
            slug="authorized-placement",
        )

        response = self.client.post(
            reverse("ads:toggle_settings"),
            {"action": "disable_placement", "placement": placement.pk},
        )

        self.assertEqual(response.status_code, 302)
        placement.refresh_from_db()
        self.assertFalse(placement.is_enabled)

    def test_placement_permission_cannot_change_global_settings(self):
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_adplacement")
        )

        response = self.client.post(
            reverse("ads:toggle_settings"), {"ads_enabled": "0"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(AdsSettings.get_solo().ads_enabled)

    def test_settings_permission_cannot_toggle_a_campaign(self):
        self.staff.user_permissions.add(
            Permission.objects.get(codename="change_adssettings")
        )
        campaign = Campaign.objects.create(name="Permission-protected campaign")

        response = self.client.post(
            reverse("ads:toggle_settings"),
            {"action": "disable_campaign", "campaign": campaign.pk},
        )

        self.assertEqual(response.status_code, 403)
        campaign.refresh_from_db()
        self.assertTrue(campaign.is_active)

    def test_campaign_permission_can_toggle_a_campaign(self):
        self.staff.user_permissions.add(
            Permission.objects.get(
                codename="change_campaign",
                content_type__app_label="ads",
                content_type__model="campaign",
            )
        )
        campaign = Campaign.objects.create(name="Authorized campaign")

        response = self.client.post(
            reverse("ads:toggle_settings"),
            {"action": "disable_campaign", "campaign": campaign.pk},
        )

        self.assertEqual(response.status_code, 302)
        campaign.refresh_from_db()
        self.assertFalse(campaign.is_active)


class AdsSettingsSecretTests(SimpleTestCase):
    def test_amazon_paapi_secret_is_encrypted_and_not_rendered_back(self):
        secret = "test-paapi-secret"
        settings_obj = AdsSettings()
        settings_obj.set_amazon_paapi_secret_key(secret)

        self.assertNotEqual(settings_obj.amazon_paapi_secret_key, secret)
        self.assertEqual(settings_obj.get_amazon_paapi_secret_key(), secret)

        rendered = AdsSettingsForm(instance=settings_obj).as_p()
        self.assertIn('type="password"', rendered)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(settings_obj.amazon_paapi_secret_key, rendered)

    def test_paapi_secret_decryption_failure_does_not_return_stored_value(self):
        settings_obj = AdsSettings(amazon_paapi_secret_key="not-encrypted")

        self.assertEqual(settings_obj.get_amazon_paapi_secret_key(), "")


class AdsCredentialMigrationTests(TestCase):
    def test_legacy_plaintext_credentials_are_encrypted_without_double_encryption(self):
        secret = "legacy-provider-secret"
        encrypted_provider = AffiliateProvider(
            name="Encrypted legacy provider", provider_type="custom"
        )
        encrypted_provider.set_api_secret("already-encrypted")
        encrypted_provider.save()
        network = AdNetwork.objects.create(
            name="Legacy ad network",
            network_type="custom",
            api_key="legacy-api-key",
            api_secret=secret,
        )
        provider = AffiliateProvider.objects.create(
            name="Legacy provider",
            provider_type="custom",
            api_key="legacy-provider-key",
            api_secret=secret,
        )
        settings_obj = AdsSettings(amazon_paapi_secret_key="legacy-paapi-secret")
        settings_obj.save()
        migration = import_module("apps.ads.migrations.0012_encrypt_ads_credentials")

        class HistoricalApps:
            @staticmethod
            def get_model(app_label: str, model_name: str):
                assert app_label == "ads"
                return {
                    "AdsSettings": AdsSettings,
                    "AdNetwork": AdNetwork,
                    "AffiliateProvider": AffiliateProvider,
                }[model_name]

        schema_editor = SimpleNamespace(connection=SimpleNamespace(alias="default"))
        migration.encrypt_existing_secrets(HistoricalApps, schema_editor)

        network.refresh_from_db()
        provider.refresh_from_db()
        settings_obj.refresh_from_db()
        encrypted_provider.refresh_from_db()
        self.assertEqual(network.get_api_key(), "legacy-api-key")
        self.assertEqual(network.get_api_secret(), secret)
        self.assertEqual(provider.get_api_key(), "legacy-provider-key")
        self.assertEqual(provider.get_api_secret(), secret)
        self.assertEqual(
            settings_obj.get_amazon_paapi_secret_key(), "legacy-paapi-secret"
        )
        self.assertEqual(encrypted_provider.get_api_secret(), "already-encrypted")
        self.assertNotEqual(network.api_secret, secret)
        self.assertNotEqual(provider.api_secret, secret)


class AdsAnalyticsPrivacyTests(TestCase):
    def test_analytics_models_do_not_have_raw_visitor_identifiers(self):
        for model in (AffiliateClick, RewardedAdView, AdEvent):
            with self.subTest(model=model.__name__):
                fields = {field.name for field in model._meta.fields}
                self.assertFalse(fields & {"ip_address", "user_agent", "session_id"})

    def test_event_tracker_hashes_identifiers_and_discards_unrecognized_metadata(self):
        secret_ip = "203.0.113.17"
        secret_agent = "private-user-agent-value"
        with patch(
            "apps.ads.services.analytics.tracker.feature_flags.ads_enabled",
            return_value=True,
        ):
            record_event(
                "impression",
                request_meta={
                    "ip": secret_ip,
                    "user_agent": secret_agent,
                    "raw_ip": secret_ip,
                    "page_url": "https://example.test/page",
                    "consent_granted": True,
                },
            )

        event = AdEvent.objects.get()
        self.assertEqual(event.request_meta["ip_hash"], hash_ip(secret_ip))
        self.assertEqual(event.request_meta["user_agent_hash"], hash_ua(secret_agent))
        self.assertNotIn(secret_ip, repr(event.request_meta))
        self.assertNotIn(secret_agent, repr(event.request_meta))
        self.assertNotIn("raw_ip", event.request_meta)
        self.assertNotIn("session_id", event.request_meta)
