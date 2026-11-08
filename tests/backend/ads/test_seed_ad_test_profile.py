"""seed_ad_test_profile: paused house campaign + draft placements, idempotent, never serving."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.ads.models import (
    AdCreative,
    AdEvent,
    AdPlacement,
    AdsSettings,
    Campaign,
    PlacementAssignment,
)


def seed() -> str:
    out = StringIO()
    call_command("seed_ad_test_profile", stdout=out)
    return out.getvalue()


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost"],
    ROOT_URLCONF="app.urls",
    SECURE_SSL_REDIRECT=False,
)
class SeedAdTestProfileTests(TestCase):
    def test_creates_a_paused_campaign_and_draft_placements_for_the_main_zones(self):
        message = seed()

        campaign = Campaign.objects.get(name="Test profile (house)")
        self.assertFalse(campaign.is_active)
        zones = set(AdPlacement.objects.values_list("zone", flat=True))
        self.assertLessEqual(
            {"header", "sidebar", "in_content", "after_content", "footer"}, zones
        )
        self.assertFalse(AdPlacement.objects.filter(is_enabled=True).exists())
        self.assertEqual(
            AdPlacement.objects.count(), PlacementAssignment.objects.count()
        )
        self.assertIn("Nothing is switched on", message)

    def test_is_idempotent(self):
        seed()
        counts = (
            Campaign.objects.count(),
            AdCreative.objects.count(),
            AdPlacement.objects.count(),
            PlacementAssignment.objects.count(),
        )

        seed()

        self.assertEqual(
            counts,
            (
                Campaign.objects.count(),
                AdCreative.objects.count(),
                AdPlacement.objects.count(),
                PlacementAssignment.objects.count(),
            ),
        )

    def test_does_not_touch_the_master_switch_and_serves_nothing(self):
        row = AdsSettings.get_solo()
        row.ads_enabled = True
        row.save()
        seed()

        with patch("apps.ads.services.zones.has_consent", return_value=True):
            html = self.client.get("/").content.decode()

        self.assertTrue(AdsSettings.get_solo().ads_enabled)
        self.assertNotIn("data-ad-zone", html)
        self.assertEqual(AdEvent.objects.count(), 0)

    def test_serves_once_deliberately_switched_on(self):
        row = AdsSettings.get_solo()
        row.ads_enabled = True
        row.save()
        seed()
        Campaign.objects.update(is_active=True)
        AdPlacement.objects.update(is_enabled=True)

        with patch("apps.ads.services.zones.has_consent", return_value=True):
            html = self.client.get("/").content.decode()

        self.assertIn('data-ad-zone="header"', html)
        self.assertIn("Mobofirm house ad", html)

    def test_keeps_a_staff_made_placement_instead_of_duplicating_it(self):
        mine = AdPlacement.objects.create(
            name="Mine", code="mine", slug="mine", zone="header", is_enabled=True
        )

        seed()

        mine.refresh_from_db()
        self.assertTrue(mine.is_enabled)
        self.assertEqual(
            AdPlacement.objects.filter(zone="header", template_reference="").count(), 1
        )
