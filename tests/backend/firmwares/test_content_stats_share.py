"""Firmware pages: real view/download totals, last update, share component and social meta."""

from __future__ import annotations

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.firmwares.tracking_models import FirmwareView

SITE = "https://example.org"
BROWSER = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


@override_settings(SITE_URL=SITE)
class FirmwareStatsAndShareTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Stat Brand", slug="stat-brand")
        self.model = Model.objects.create(
            brand=self.brand, name="Stat Phone", slug="stat-phone", model_code="ST-1"
        )
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="stat.zip",
            stored_file_path="/firmware/stat.zip",
            build_number="STAT.240101",
            brand=self.brand,
            model=self.model,
            is_verified=True,
        )
        OfficialFirmware.objects.filter(pk=self.firmware.pk).update(
            view_count=1234,
            download_count=56,
            updated_at=timezone.now() - timedelta(days=3),
        )
        self.firmware.refresh_from_db()
        self.url = reverse(
            "firmwares:firmware_detail", args=["official", self.firmware.pk]
        )

    def fetch(self, url=None, client=None):
        return (client or self.client).get(url or self.url, **BROWSER)

    # -- totals and last update -------------------------------------------------
    def test_detail_shows_the_stored_totals_and_the_last_update(self):
        response = self.fetch()

        # The visit itself is counted before the page is rendered.
        self.assertContains(response, 'title="1235 views"')
        self.assertContains(response, 'title="56 downloads"')
        self.assertContains(response, "1.2k")
        self.assertContains(
            response,
            f'<time datetime="{self.firmware.updated_at.isoformat()}"',
        )
        self.assertContains(response, "Last updated")

    def test_listing_cards_show_views_downloads_and_update(self):
        response = self.client.get(
            reverse(
                "firmwares:model_detail", args=[self.brand.slug, self.model.slug]
            ),
            **BROWSER,
        )

        self.assertContains(response, 'title="1234 views"')
        self.assertContains(response, 'title="56 downloads"')
        self.assertContains(response, "Updated")

    def test_stats_api_and_page_report_the_same_numbers(self):
        self.fetch()
        stats = self.client.get(
            reverse("firmwares:api_firmware_stats", args=["official", self.firmware.pk])
        ).json()

        self.assertEqual(stats, {"views": 1235, "downloads": 56})

    def test_views_download_counts_are_separate_fields(self):
        self.fetch()
        self.firmware.refresh_from_db()

        self.assertEqual(self.firmware.view_count, 1235)
        self.assertEqual(self.firmware.download_count, 56)

    # -- counting rules ---------------------------------------------------------
    def test_each_new_visitor_counts_once_and_repeat_views_do_not(self):
        first, second = Client(), Client()

        for client in (first, first, second, second, first):
            self.fetch(client=client)

        self.firmware.refresh_from_db()
        self.assertEqual(self.firmware.view_count, 1236)
        self.assertEqual(FirmwareView.objects.count(), 2)

    def test_the_increment_is_one_atomic_relative_update(self):
        with CaptureQueriesContext(connection) as queries:
            self.fetch()

        updates = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].startswith('UPDATE "firmwares_officialfirmware"')
        ]
        self.assertEqual(len(updates), 1, updates)
        self.assertRegex(updates[0], r'"view_count" = \("firmwares_officialfirmware"."view_count" \+ 1\)')

    def test_counting_a_view_never_touches_the_update_time(self):
        before = self.firmware.updated_at

        self.fetch()
        self.firmware.refresh_from_db()

        self.assertEqual(self.firmware.updated_at, before)

    def test_unapproved_firmware_is_not_visible_or_counted_even_for_staff(self):
        hidden = OfficialFirmware.objects.create(
            original_file_name="hidden.zip",
            stored_file_path="/firmware/hidden.zip",
            brand=self.brand,
            model=self.model,
            is_verified=False,
        )
        staff = get_user_model().objects.create_superuser(
            username="stat-staff", email="stat-staff@example.test"
        )
        client = Client()
        client.force_login(staff)

        response = self.fetch(
            reverse("firmwares:firmware_detail", args=["official", hidden.pk]),
            client=client,
        )

        self.assertEqual(response.status_code, 404)
        hidden.refresh_from_db()
        self.assertEqual(hidden.view_count, 0)
        self.assertFalse(FirmwareView.objects.filter(object_id=hidden.pk).exists())

    # -- share component and social meta -----------------------------------------
    def test_detail_offers_the_shared_component_with_the_canonical_url(self):
        html = self.fetch().content.decode()
        canonical = f"{SITE}{self.url}"

        self.assertIn(f'data-share-url="{canonical}"', html)
        self.assertIn(f'data-copy="{canonical}"', html)
        self.assertIn("Share this firmware", html)
        for network in ("X", "Facebook", "WhatsApp", "Telegram", "LinkedIn", "Email"):
            self.assertRegex(html, rf'class="share__link"[^>]*>(?:<i[^>]*></i>){network}')
        self.assertIn("x.com/intent/post?url=https%3A%2F%2Fexample.org%2Ffirmwares", html)
        self.assertEqual(html.count('rel="noopener noreferrer"'), 5)

    def test_download_page_shares_the_detail_page_never_a_token_url(self):
        response = self.fetch(
            reverse("firmwares:firmware_download", args=["official", self.firmware.pk])
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(f'data-share-url="{SITE}{self.url}"', html)
        self.assertNotIn("token=", re.search(r'data-share-url="([^"]*)"', html).group(1))
        self.assertContains(response, 'title="56 downloads"')

    def test_detail_head_has_open_graph_and_twitter_metadata(self):
        html = self.fetch().content.decode()

        self.assertRegex(html, r'<meta property="og:title" content="[^"]*Stat Phone')
        self.assertIn(f'<meta property="og:url" content="{SITE}{self.url}">', html)
        self.assertIn('<meta property="og:type"', html)
        self.assertRegex(html, r'<meta name="twitter:card" content="[^"]+"')
        self.assertIn(f'<link rel="canonical" href="{SITE}{self.url}">', html)

    # -- admin --------------------------------------------------------------------
    def test_admin_list_has_sortable_view_download_and_update_columns(self):
        admin_user = get_user_model().objects.create_superuser(
            username="stat-admin", email="stat-admin@example.test"
        )
        client = Client()
        client.force_login(admin_user)
        changelist = reverse("admin:firmwares_officialfirmware_changelist")

        response = client.get(changelist)

        self.assertEqual(response.status_code, 200)
        for header in ("Download count", "View count", "Updated at"):
            self.assertContains(response, header)
        columns = list(response.context["cl"].list_display)
        for field in ("view_count", "download_count", "updated_at"):
            ordered = client.get(changelist, {"o": str(columns.index(field) + 1)})
            self.assertEqual(ordered.status_code, 200, field)
