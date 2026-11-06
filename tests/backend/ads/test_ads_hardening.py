from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.ads.models import AdsSettings
from apps.ads.templatetags.ad_tags import _safe_click_url, _validate_ad_creative_code


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
        response = self.client.post(
            reverse("ads:toggle_settings"), {"ads_enabled": "0"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AdsSettings.get_solo().ads_enabled)
