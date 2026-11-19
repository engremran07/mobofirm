from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.seo import ledger
from apps.seo.models import ChangeLedger, SEOSettings
from apps.seo.sitemap_report import build_report, previous_robots, robots_history
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_staff


@override_settings(SITE_URL=SITE)
class SitemapsScreenTests(TestCase):
    def setUp(self):
        self.data = make_catalog()
        self.admin = make_staff("boss", superuser=True)
        self.client.force_login(self.admin)
        self.url = reverse("seo:sitemaps")

    def test_requires_staff(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_lists_each_section_with_counts_files_lastmod_and_limits(self):
        response = self.client.get(self.url)

        self.assertContains(response, "console-staff-page")
        self.assertContains(response, "Sitemaps")
        report = response.context["report"]
        names = {item.section for item in report.sections}
        self.assertTrue({"brands", "models", "firmware", "chipsets", "blog"} <= names)
        brands = next(item for item in report.sections if item.section == "brands")
        self.assertEqual(brands.url_count, 1)
        self.assertEqual(brands.pages, 1)
        self.assertIsNotNone(brands.lastmod)
        self.assertTrue(report.ok)
        self.assertContains(response, "sitemap-models-nova-1.xml")
        self.assertContains(response, "Video sitemap: off")

    def test_live_validation_samples_the_resolver_and_finds_no_conflicts(self):
        response = self.client.get(self.url, {"validate": "1"})

        report = response.context["report"]
        self.assertTrue(report.validated_live)
        self.assertTrue(all(item.sampled for item in report.sections))
        self.assertTrue(report.ok, [item.conflicts for item in report.sections])

    def test_duplicate_urls_across_files_are_reported(self):
        report = build_report(SEOSettings.get_solo(), SITE)
        self.assertEqual(report.duplicates, [])

        from apps.seo import sitemap_registry
        from apps.seo.sitemap_registry import get_raw_sitemaps

        brand_class = get_raw_sitemaps()["brands"]
        sitemap_registry.register_sitemap("brands_copy", brand_class)
        self.addCleanup(sitemap_registry.unregister_sitemap, "brands_copy")

        again = build_report(SEOSettings.get_solo(), SITE)

        self.assertTrue(again.duplicates)
        self.assertFalse(again.ok)

    def test_robots_preview_tester_reports_rule_and_agent(self):
        blocked = self.client.get(
            self.url, {"url": "/admin/login/", "agent": "Googlebot"}
        )
        allowed = self.client.get(self.url, {"url": "/firmwares/", "agent": "GPTBot"})
        param = self.client.get(
            self.url, {"url": f"{SITE}/firmwares/browse/?sort=new", "agent": "bingbot"}
        )

        self.assertContains(blocked, "robots.txt preview")
        self.assertFalse(blocked.context["tested"].allowed)
        self.assertIn("Disallow: /admin/", blocked.context["tested"].reason)
        self.assertFalse(allowed.context["tested"].allowed)  # GPTBot blocked by default
        self.assertIn("Disallow: /", allowed.context["tested"].reason)
        self.assertFalse(param.context["tested"].allowed)
        self.assertEqual(param.context["tested"].url, "/firmwares/browse/?sort=new")

    def test_an_invalid_agent_token_falls_back_to_googlebot(self):
        response = self.client.get(self.url, {"url": "/", "agent": "<script>"})

        self.assertEqual(response.context["test_agent"], "Googlebot")

    def test_settings_change_appears_in_history_and_diff_through_the_ledger(self):
        row = SEOSettings.get_solo()
        before = ledger.serialize(row)
        row.allow_ai_training_bots = True
        row.save()
        ledger.record_manual(
            user=self.admin,
            action=ChangeLedger.Action.UPDATE,
            instance=row,
            before=before,
            after=ledger.serialize(row),
        )

        response = self.client.get(self.url)

        self.assertEqual(len(robots_history()), 1)
        self.assertIsNotNone(previous_robots(row, SITE))
        self.assertTrue(response.context["has_previous"])
        self.assertTrue(
            any(
                line.startswith("-User-agent: GPTBot")
                for line in response.context["diff"]
            )
        )
        changes = response.context["history"][0]["changes"]
        self.assertEqual(changes, [("allow_ai_training_bots", False, True)])
        self.assertContains(response, "allow_ai_training_bots")

    def test_unrelated_settings_changes_do_not_enter_the_robots_history(self):
        row = SEOSettings.get_solo()
        before = ledger.serialize(row)
        row.title_separator = " - "
        row.save()
        ledger.record_manual(
            user=self.admin,
            action=ChangeLedger.Action.UPDATE,
            instance=row,
            before=before,
            after=ledger.serialize(row),
        )

        self.assertEqual(robots_history(), [])
        self.assertFalse(self.client.get(self.url).context["has_previous"])

    def test_the_screen_is_linked_from_the_seo_tabs(self):
        response = self.client.get(reverse("seo:dashboard"))

        self.assertContains(response, self.url)
