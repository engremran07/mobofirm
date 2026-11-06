from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.ads.api import track_affiliate_click_sync
from apps.ads.models import (
    AdCreative,
    AdEvent,
    AdNetwork,
    AdPlacement,
    AdsSettings,
    AdUnit,
    AffiliateClick,
    AffiliateProduct,
    AffiliateProvider,
    Campaign,
    PlacementAssignment,
    RewardedAdConfig,
)
from apps.ads.services.rotation.engine import choose_creative
from apps.ads.templatetags.ad_tags import render_ad_slot


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class AdsApiTests(TestCase):
    def setUp(self) -> None:
        # Use AdsSettings to enable/disable ads
        ads_settings = AdsSettings.get_solo()
        ads_settings.ads_enabled = True
        ads_settings.save()
        self.client = Client()

    def create_affiliate_product(self):
        provider = AffiliateProvider.objects.create(
            name="Test affiliate provider", provider_type="custom"
        )
        return AffiliateProduct.objects.create(
            provider=provider,
            name="Test accessory",
            slug="test-accessory",
            external_id="test-accessory-1",
            product_url="https://example.test/accessory",
        )

    def test_affiliate_click_updates_product_and_records_event(self):
        product = self.create_affiliate_product()

        result = track_affiliate_click_sync(product_id=product.pk)

        self.assertEqual(result["status"], "success")
        self.assertEqual(AffiliateClick.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.clicks, 1)

    def test_affiliate_click_database_failure_rolls_back_partial_event(self):
        product = self.create_affiliate_product()
        original_filter = AffiliateProduct.objects.filter

        with (
            patch.object(
                AffiliateProduct.objects,
                "filter",
                side_effect=[
                    original_filter(id=product.pk),
                    OperationalError("simulated click counter failure"),
                ],
            ),
            self.assertLogs("apps.ads.api", level="ERROR"),
        ):
            result = track_affiliate_click_sync(product_id=product.pk)

        self.assertEqual(result["error"], "tracking_unavailable")
        self.assertEqual(AffiliateClick.objects.count(), 0)
        product.refresh_from_db()
        self.assertEqual(product.clicks, 0)

    @patch(
        "apps.ads.api.AdsSettings.get_solo",
        side_effect=OperationalError("simulated settings failure"),
    )
    def test_affiliate_products_settings_failure_returns_service_unavailable(
        self, _mock_get_solo
    ):
        with self.assertLogs("apps.ads.api", level="ERROR"):
            response = self.client.get(reverse("ads:get_affiliate_products"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"ok": False, "error": "settings_unavailable"}
        )

    def test_affiliate_context_lookup_failure_returns_service_unavailable(self):
        with (
            patch(
                "apps.ads.api.get_affiliate_products_settings",
                return_value={"enabled": True},
            ),
            patch(
                "apps.firmwares.models.Brand.objects.filter",
                side_effect=OperationalError("simulated brand lookup failure"),
            ),
            self.assertLogs("apps.ads.views", level="ERROR"),
        ):
            response = self.client.get(
                reverse("ads:get_affiliate_products"), {"brand_id": "1"}
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"ok": False, "error": "products_unavailable"}
        )

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    @patch(
        "apps.ads.api.track_affiliate_click_sync",
        return_value={"status": "error", "error": "tracking_unavailable"},
    )
    def test_affiliate_click_tracking_failure_returns_service_unavailable(
        self, _mock_track_click, _mock_consent
    ):
        response = self.client.post(
            reverse("ads:track_affiliate_click"), {"product_id": "1"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"ok": False, "error": "tracking_unavailable"}
        )

    @patch("apps.ads.api.track_affiliate_click_sync")
    def test_affiliate_click_without_consent_stores_nothing(self, track_click):
        response = self.client.post(
            reverse("ads:track_affiliate_click"), {"product_id": "1"}
        )

        self.assertEqual(response.json()["skipped"], "no_consent")
        track_click.assert_not_called()

    def test_fill_ad_disabled_returns_403(self):
        ads_settings = AdsSettings.get_solo()
        ads_settings.ads_enabled = False
        ads_settings.save()
        url = reverse("ads:fill_ad")
        res = self.client.get(url, {"placement": "missing"})
        assert res.status_code == 403

    @patch("apps.ads.views._has_ads_consent", return_value=True)
    def test_fill_ad_returns_creative(self, mock_consent):
        campaign = Campaign.objects.create(name="C1")
        placement = AdPlacement.objects.create(
            name="Top",
            slug="top",
            code="top",
            allowed_types="banner",
            allowed_sizes="300x250",
            page_context="blog_detail",
        )
        creative = AdCreative.objects.create(
            campaign=campaign,
            name="Banner",
            creative_type="banner",
            image_url="https://example.com/banner.png",
            click_url="https://example.com",
        )
        PlacementAssignment.objects.create(placement=placement, creative=creative)
        url = reverse("ads:fill_ad")
        res = self.client.get(url, {"placement": placement.slug})
        assert res.status_code == 200
        payload = res.json()
        # If ok=False, the response has an error structure
        if payload.get("ok", True) is False:
            self.fail(f"fill_ad failed: {payload.get('error')}")
        assert "creative" in payload, f"Expected 'creative' in payload, got: {payload}"
        # creative is a dict with the creative details including 'creative' ID
        creative_data = payload["creative"]
        assert creative_data["creative"] == creative.pk
        assert creative_data["type"] == "banner"

    def test_click_requires_consent(self):
        placement = AdPlacement.objects.create(
            name="Top",
            slug="top",
            code="top",
            allowed_types="banner",
            allowed_sizes="300x250",
            page_context="blog_detail",
        )
        url = reverse("ads:record_click")
        res = self.client.post(url, {"placement": placement.slug})
        assert res.status_code == 200
        assert "skipped" in res.json()

    def test_rotation_allows_string_true_consent(self):
        campaign = Campaign.objects.create(name="C1", type="direct")
        placement = AdPlacement.objects.create(
            name="Top",
            slug="top",
            code="top",
            allowed_types="banner",
            allowed_sizes="300x250",
            page_context="blog_detail",
        )
        creative = AdCreative.objects.create(
            campaign=campaign,
            name="Banner",
            creative_type="banner",
            image_url="https://example.com/banner.png",
            click_url="https://example.com",
        )
        PlacementAssignment.objects.create(placement=placement, creative=creative)
        context = {"consent_ads": "1", "page_context": placement.page_context}
        selected = choose_creative(placement, context)
        assert selected is not None

    def test_rotation_blocks_string_false_consent(self):
        campaign = Campaign.objects.create(name="C1", type="direct")
        placement = AdPlacement.objects.create(
            name="Top",
            slug="top",
            code="top",
            allowed_types="banner",
            allowed_sizes="300x250",
            page_context="blog_detail",
        )
        creative = AdCreative.objects.create(
            campaign=campaign,
            name="Banner",
            creative_type="banner",
            image_url="https://example.com/banner.png",
            click_url="https://example.com",
        )
        PlacementAssignment.objects.create(placement=placement, creative=creative)
        context = {"consent_ads": "0", "page_context": placement.page_context}
        selected = choose_creative(placement, context)
        assert selected is None

    def test_locked_inactive_campaign_is_not_eligible_for_rotation(self):
        campaign = Campaign.objects.create(
            name="Locked inactive campaign",
            type="direct",
            is_active=False,
            locked=True,
        )
        placement = AdPlacement.objects.create(
            name="Inactive campaign test placement",
            slug="inactive-campaign-test",
            code="inactive-campaign-test",
            allowed_types="banner",
            page_context="blog_detail",
        )
        creative = AdCreative.objects.create(
            campaign=campaign,
            name="Inactive campaign creative",
            creative_type="banner",
            image_url="https://example.com/banner.png",
        )
        PlacementAssignment.objects.create(placement=placement, creative=creative)

        selected = choose_creative(
            placement,
            {"consent_ads": True, "page_context": placement.page_context},
        )

        self.assertIsNone(selected)

    def test_unfilled_public_placement_has_csp_compatible_disclosure(self):
        placement = AdPlacement.objects.create(
            name="Homepage banner",
            slug="homepage-banner",
            code="after-hero",
        )

        markup = render_ad_slot({}, placement.code)

        self.assertIn('aria-label="Advertisement: Homepage banner"', markup)
        self.assertIn("Advertisement", markup)
        self.assertNotIn("style=", markup)
        self.assertNotIn("Your Ad Here", markup)


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class DemoAdPreviewTests(TestCase):
    def test_preview_requires_an_ads_manager(self):
        response = self.client.get(reverse("ads:demo_preview"))

        self.assertEqual(response.status_code, 302)

    def test_preview_renders_disabled_database_creatives_without_serving_ads(self):
        user = get_user_model().objects.create_user(
            username="ad-preview-manager",
            email="ad-preview-manager@example.test",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(user)

        with patch(
            "apps.ads.services.scanner.scan_templates_for_placements",
            return_value={"status": "success", "scanned": 0, "discoveries": 0},
        ):
            call_command("setup_test_environment", stdout=StringIO())

        response = self.client.get(reverse("ads:demo_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode().count(
                '<article class="ad-preview-card" data-ad-format='
            ),
            len(AdUnit.AD_FORMATS),
        )
        for format_code, _ in AdUnit.AD_FORMATS:
            self.assertContains(response, f'data-ad-format="{format_code}"')
        self.assertEqual(
            response.content.decode().count('data-demo-preview="true"'),
            len(AdUnit.AD_FORMATS),
        )
        self.assertContains(response, "Rendered from the saved demo creative")
        self.assertContains(
            response, "No provider, impression, click, or reward is triggered."
        )
        self.assertFalse(AdNetwork.objects.filter(is_enabled=True).exists())
        self.assertFalse(PlacementAssignment.objects.filter(is_enabled=True).exists())
        self.assertEqual(AdEvent.objects.count(), 0)

    def test_public_renderer_does_not_render_disabled_local_demo_creatives(self):
        with patch(
            "apps.ads.services.scanner.scan_templates_for_placements",
            return_value={"status": "success", "scanned": 0, "discoveries": 0},
        ):
            call_command("setup_test_environment", stdout=StringIO())

        markup = render_ad_slot({}, "demo-display")

        self.assertEqual(markup, "")


class DemoAdSetupTests(TestCase):
    def test_setup_creates_disabled_format_profiles_and_is_idempotent(self):
        scan_result = {"status": "success", "scanned": 12, "discoveries": 8}
        with patch(
            "apps.ads.services.scanner.scan_templates_for_placements",
            return_value=scan_result,
        ) as scan_templates:
            call_command("setup_test_environment", stdout=StringIO())
            creative = AdCreative.objects.get(
                campaign__name="Mobofirm Local Demo Campaign",
                name="Local Demo Display Banner",
            )
            creative.html = "<p>Stale local demo content</p>"
            creative.save(update_fields=["html"])
            call_command("setup_test_environment", stdout=StringIO())

        self.assertEqual(AdNetwork.objects.count(), 1)
        network = AdNetwork.objects.get()
        self.assertFalse(network.is_enabled)
        self.assertEqual(network.api_key, "")
        self.assertEqual(network.header_script, "")
        self.assertEqual(
            set(
                AdUnit.objects.filter(network=network).values_list(
                    "ad_format", flat=True
                )
            ),
            {format_code for format_code, _ in AdUnit.AD_FORMATS},
        )
        self.assertFalse(AdUnit.objects.filter(is_enabled=True).exists())
        self.assertEqual(AffiliateProvider.objects.count(), 1)
        self.assertFalse(AffiliateProvider.objects.get().is_enabled)
        self.assertFalse(Campaign.objects.filter(is_active=True).exists())
        self.assertFalse(AdCreative.objects.filter(is_active=True).exists())
        self.assertFalse(AdPlacement.objects.filter(is_active=True).exists())
        self.assertFalse(PlacementAssignment.objects.filter(is_active=True).exists())
        self.assertFalse(RewardedAdConfig.objects.filter(is_enabled=True).exists())
        self.assertEqual(scan_templates.call_count, 2)
        creative.refresh_from_db()
        self.assertIn("Rendered from the saved demo creative", creative.html)

    def test_setup_refuses_a_non_local_database(self):
        with (
            patch(
                "apps.ads.management.commands.setup_test_environment.connection",
                SimpleNamespace(
                    vendor="postgresql",
                    settings_dict={"HOST": "remote-db.example.invalid"},
                ),
            ),
            self.assertRaises(CommandError),
        ):
            call_command("setup_test_environment", stdout=StringIO())
