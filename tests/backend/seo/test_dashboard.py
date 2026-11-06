from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.seo import ledger
from apps.seo.models import (
    ChangeLedger,
    CrawlRun,
    Issue,
    Redirect,
    SEOJob,
    SEOSettings,
)
from tests.backend.seo.seo_helpers import SITE, make_catalog

User = get_user_model()


def staff(username="staff", *perms, superuser=False):
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="pw-123456-x"
    )
    user.is_staff = True
    user.is_superuser = superuser
    user.save()
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


@override_settings(SITE_URL=SITE)
class ControlCenterAccessTests(TestCase):
    def test_every_page_requires_staff(self):
        regular = User.objects.create_user(
            username="regular", email="r@example.com", password="pw-123456-x"
        )
        urls = [
            reverse(f"seo:{name}")
            for name in (
                "dashboard",
                "inventory",
                "inspect",
                "issues",
                "ledger",
                "jobs",
                "settings",
            )
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(regular)
        for url in urls:
            with self.subTest(url=url, user="regular"):
                self.assertEqual(self.client.get(url).status_code, 302)

    def test_staff_can_read_every_page(self):
        make_catalog()
        self.client.force_login(staff())
        for name in (
            "dashboard",
            "inventory",
            "inspect",
            "issues",
            "ledger",
            "jobs",
            "settings",
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(f"seo:{name}"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "SEO sections")

    def test_mutating_endpoints_are_post_only_csrf_protected_and_permissioned(self):
        user = staff("limited")
        self.client.force_login(user)
        entry = ChangeLedger.objects.create(
            idempotency_key="k",
            kind="redirect_create",
            action="create",
            target_model="seo.redirect",
            after={"source": "/s/", "target": "/t/"},
            reason="x",
            source="test",
        )
        for name, kwargs in (
            ("start_crawl", {}),
            ("process_jobs", {}),
            ("ledger_action", {"pk": entry.pk, "action": "approve"}),
        ):
            with self.subTest(endpoint=name):
                url = reverse(f"seo:{name}", kwargs=kwargs)
                self.assertEqual(self.client.get(url).status_code, 405)
                self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                reverse("seo:settings"), {"seo_enabled": "on"}
            ).status_code,
            403,
        )
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.PROPOSED)

    def test_csrf_is_enforced_on_dashboard_posts(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(staff("admin2", superuser=True))

        self.assertEqual(strict.post(reverse("seo:start_crawl")).status_code, 403)

    def test_dashboard_forms_carry_csrf_tokens(self):
        self.client.force_login(staff("boss", superuser=True))

        response = self.client.get(reverse("seo:dashboard"))

        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "Run crawl and audit now")


@override_settings(SITE_URL=SITE)
class ControlCenterContentTests(TestCase):
    def setUp(self):
        self.user = staff("boss", superuser=True)
        self.client.force_login(self.user)

    def test_dashboard_reports_measured_state_only(self):
        response = self.client.get(reverse("seo:dashboard"))

        self.assertContains(response, "No completed crawl yet")
        self.assertNotContains(response, "SERP")

        CrawlRun.objects.create(
            base_url=SITE,
            status=CrawlRun.Status.COMPLETED,
            pages_crawled=10,
            pages_with_errors=2,
        )
        Issue.objects.create(
            code="missing_title",
            severity="error",
            url=f"{SITE}/a/",
            message="No title",
            fingerprint="f1",
        )
        response = self.client.get(reverse("seo:dashboard"))
        self.assertContains(response, "80%")
        self.assertContains(response, "10 crawled pages")

    def test_inventory_lists_routes_and_flags_policy(self):
        make_catalog()

        response = self.client.get(reverse("seo:inventory"))

        self.assertContains(response, "/firmwares/nova/")
        self.assertContains(response, "noindex")
        self.assertContains(response, "Discovery never makes a page indexable")

    def test_inspector_shows_intent_provenance_and_missing_crawl(self):
        make_catalog()

        response = self.client.get(
            reverse("seo:inspect"), {"url": "/firmwares/nova/atlas-one/"}
        )

        self.assertContains(response, "What the resolver intends")
        self.assertContains(response, "Atlas One")
        self.assertContains(response, "has not been crawled yet")
        self.assertContains(response, "(object)")

    def test_inspector_handles_unknown_routes(self):
        response = self.client.get(reverse("seo:inspect"), {"url": "/nope/nope/"})

        self.assertContains(response, "No route matches this path")

    def test_issue_list_filters_and_supports_htmx_partials(self):
        for index, severity in enumerate(("error", "warning")):
            Issue.objects.create(
                code=f"code_{severity}",
                severity=severity,
                url=f"{SITE}/{index}/",
                message=f"message {severity}",
                fingerprint=f"fp{index}",
            )

        page = self.client.get(reverse("seo:issues"), {"severity": "error"})
        partial = self.client.get(
            reverse("seo:issues"), {"severity": "warning"}, HTTP_HX_REQUEST="true"
        )

        self.assertContains(page, "message error")
        self.assertNotContains(page, "message warning")
        self.assertContains(partial, "message warning")
        self.assertNotContains(partial, "<html")

    def test_ledger_page_approves_rejects_and_rolls_back(self):
        pending = ChangeLedger.objects.create(
            idempotency_key="p1",
            kind="redirect_create",
            action="create",
            target_model="seo.redirect",
            target_label="/s/ -> /t/",
            after={
                "source": "/s/",
                "target": "/t/",
                "is_permanent": True,
                "is_active": True,
            },
            reason="moved",
            source="test",
            requires_approval=True,
        )
        rejected = ChangeLedger.objects.create(
            idempotency_key="p2",
            kind="redirect_create",
            action="create",
            target_model="seo.redirect",
            after={"source": "/r/", "target": "/q/"},
            reason="moved",
            source="test",
        )

        page = self.client.get(reverse("seo:ledger"), {"status": "proposed"})
        self.assertContains(page, "Approve and apply")

        self.client.post(reverse("seo:ledger_action", args=[pending.pk, "approve"]))
        self.client.post(reverse("seo:ledger_action", args=[rejected.pk, "reject"]))
        pending.refresh_from_db()
        rejected.refresh_from_db()
        self.assertEqual(pending.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(pending.decided_by, self.user)
        self.assertEqual(rejected.status, ChangeLedger.Status.REJECTED)
        self.assertTrue(Redirect.objects.filter(source="/s/").exists())

        self.client.post(reverse("seo:ledger_action", args=[pending.pk, "rollback"]))
        pending.refresh_from_db()
        self.assertEqual(pending.status, ChangeLedger.Status.ROLLED_BACK)
        self.assertFalse(Redirect.objects.filter(source="/s/").exists())

    def test_invalid_ledger_actions_and_states_are_reported(self):
        entry = ChangeLedger.objects.create(
            idempotency_key="x",
            kind="redirect_create",
            action="create",
            target_model="seo.redirect",
            after={},
            reason="x",
            source="t",
        )

        self.assertEqual(
            self.client.post(
                reverse("seo:ledger_action", args=[entry.pk, "explode"])
            ).status_code,
            404,
        )
        response = self.client.post(
            reverse("seo:ledger_action", args=[entry.pk, "rollback"]), follow=True
        )
        self.assertContains(response, "Only applied changes can be rolled back")

    def test_jobs_page_shows_failures_and_can_process(self):
        SEOJob.objects.create(
            kind="mystery", job_key="m1", status=SEOJob.Status.FAILED, last_error="boom"
        )
        pending = SEOJob.objects.create(kind="mystery", job_key="m2")

        self.assertContains(self.client.get(reverse("seo:jobs")), "boom")
        response = self.client.post(reverse("seo:process_jobs"), follow=True)

        pending.refresh_from_db()
        self.assertEqual(pending.status, SEOJob.Status.FAILED)
        self.assertContains(response, "Processed")

    def test_crawl_button_reports_when_unavailable(self):
        with override_settings(SITE_URL=""):
            response = self.client.post(reverse("seo:start_crawl"), follow=True)

        self.assertContains(response, "Configure SITE_URL")

    def test_settings_page_saves_validates_and_ledgers_changes(self):
        page = self.client.get(reverse("seo:settings"))
        self.assertContains(page, "Protected policies")
        form = {
            field.name: field.value()
            for field in page.context["form"]
            if field.value() not in (None, False)
        }
        form["autonomy_mode"] = SEOSettings.AutonomyMode.SAFE_AUTOPILOT
        form["allow_ai_training_bots"] = "on"

        response = self.client.post(reverse("seo:settings"), form, follow=True)

        self.assertContains(response, "SEO settings saved")
        row = SEOSettings.get_solo()
        self.assertEqual(row.autonomy_mode, SEOSettings.AutonomyMode.SAFE_AUTOPILOT)
        self.assertTrue(row.allow_ai_training_bots)
        entry = ChangeLedger.objects.get(target_model="seo.seosettings")
        self.assertEqual(entry.after["autonomy_mode"], "safe_autopilot")
        self.assertEqual(entry.before["autonomy_mode"], "suggest")
        ledger.rollback(entry, self.user)
        self.assertEqual(SEOSettings.get_solo().autonomy_mode, "suggest")

    def test_enabling_indexnow_requires_a_key_and_can_generate_one(self):
        form = {
            "seo_enabled": "on",
            "sitemap_enabled": "on",
            "auto_meta_enabled": "on",
            "auto_schema_enabled": "on",
            "title_separator": " | ",
            "autonomy_mode": "suggest",
            "min_firmware_for_model_index": 1,
            "min_models_for_brand_index": 1,
            "min_models_for_chipset_index": 1,
            "min_posts_for_tag_index": 1,
            "crawl_max_pages": 50,
            "crawl_delay_ms": 0,
            "allow_ai_search_bots": "on",
            "allow_ai_user_agents": "on",
            "indexnow_enabled": "on",
        }

        rejected = self.client.post(reverse("seo:settings"), form)
        self.assertContains(rejected, "A key is required to enable IndexNow")

        accepted = self.client.post(
            reverse("seo:settings"),
            {**form, "generate_indexnow_key": "on"},
            follow=True,
        )
        self.assertContains(accepted, "SEO settings saved")
        key = SEOSettings.get_solo().indexnow_key
        self.assertEqual(len(key), 32)
        self.assertEqual(self.client.get(f"/{key}.txt").content.decode(), key)


class AdminIntegrationTests(TestCase):
    def setUp(self):
        self.user = staff("root", superuser=True)
        self.client.force_login(self.user)

    def test_admin_redirect_edits_are_ledgered_and_reversible(self):
        add = self.client.post(
            reverse("admin:seo_redirect_add"),
            {
                "source": "/admin-made/",
                "target": "/dest/",
                "is_permanent": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(add.status_code, 302)
        redirect = Redirect.objects.get(source="/admin-made/")
        created = ChangeLedger.objects.get(kind="manual_edit", action="create")
        self.assertEqual(created.status, ChangeLedger.Status.APPLIED)

        change = self.client.post(
            reverse("admin:seo_redirect_change", args=[redirect.pk]),
            {
                "source": "/admin-made/",
                "target": "/other/",
                "is_permanent": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(change.status_code, 302)
        updated = ChangeLedger.objects.get(kind="manual_edit", action="update")
        self.assertEqual(updated.before, {"target": "/dest/"})

        ledger.rollback(updated, self.user)
        redirect.refresh_from_db()
        self.assertEqual(redirect.target, "/dest/")

        self.client.post(
            reverse("admin:seo_redirect_delete", args=[redirect.pk]), {"post": "yes"}
        )
        self.assertFalse(Redirect.objects.exists())
        deleted = ChangeLedger.objects.get(kind="manual_edit", action="delete")
        ledger.rollback(deleted, self.user)
        self.assertTrue(Redirect.objects.filter(source="/admin-made/").exists())

    def test_admin_rejects_unsafe_redirect_targets(self):
        response = self.client.post(
            reverse("admin:seo_redirect_add"),
            {"source": "/x/", "target": "//evil.invalid/", "is_active": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Redirect.objects.exists())

    def test_ledger_admin_actions_require_permission_and_work(self):
        entry = ChangeLedger.objects.create(
            idempotency_key="adm",
            kind="redirect_create",
            action="create",
            target_model="seo.redirect",
            after={
                "source": "/q/",
                "target": "/w/",
                "is_permanent": True,
                "is_active": True,
            },
            reason="x",
            source="test",
        )

        self.client.post(
            reverse("admin:seo_changeledger_changelist"),
            {"action": "approve_selected", "_selected_action": [entry.pk]},
        )

        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)

    def test_every_seo_model_is_registered_and_changelists_render(self):
        for name in (
            "metadata",
            "redirect",
            "seosettings",
            "crawlrun",
            "pageobservation",
            "issue",
            "changeledger",
            "seojob",
            "linkableentity",
        ):
            with self.subTest(model=name):
                self.assertEqual(
                    self.client.get(
                        reverse(f"admin:seo_{name}_changelist")
                    ).status_code,
                    200,
                )


class IntegrationStatusTests(TestCase):
    def test_every_integration_is_disabled_by_default_with_a_clear_status(self):
        from apps.seo.integrations.status import integration_statuses

        statuses = {item.key: item for item in integration_statuses()}

        self.assertEqual(statuses["indexnow"].state, "disabled")
        self.assertEqual(statuses["search_console"].state, "disabled")
        self.assertEqual(statuses["pagespeed"].state, "disabled")
        self.assertEqual(statuses["bing"].state, "unsupported")

    def test_enabled_without_credentials_is_not_configured_never_ready(self):
        from apps.seo.integrations import pagespeed, search_console

        row = SEOSettings.get_solo()
        row.pagespeed_enabled = row.search_console_enabled = True
        row.save()

        self.assertEqual(pagespeed.status()[0], "not_configured")
        self.assertEqual(search_console.status()[0], "not_configured")
        with self.assertRaises(pagespeed.PageSpeedError):
            pagespeed.run("https://example.org/")
        with self.assertRaises(search_console.SearchConsoleError):
            from datetime import date

            search_console.search_performance(date(2026, 1, 1), date(2026, 1, 2))

    @override_settings(SEO_PAGESPEED_API_KEY="k", SITE_URL=SITE)
    def test_pagespeed_parses_real_payloads_through_the_safe_fetcher(self):
        from apps.seo.fetcher import FetchResult
        from apps.seo.integrations import pagespeed

        row = SEOSettings.get_solo()
        row.pagespeed_enabled = True
        row.save()
        payload = {
            "lighthouseResult": {
                "categories": {"performance": {"score": 0.91}},
                "audits": {
                    "largest-contentful-paint": {"numericValue": 2100.0},
                    "cumulative-layout-shift": {"numericValue": 0.02},
                },
            },
            "loadingExperience": {
                "metrics": {"INTERACTION_TO_NEXT_PAINT": {"percentile": 180}}
            },
        }
        fake = mock.Mock()
        fake.fetch.return_value = FetchResult(
            "u", "u", 200, {}, json.dumps(payload).encode()
        )

        with mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake):
            report = pagespeed.run(f"{SITE}/")

        self.assertEqual(report.performance_score, 0.91)
        self.assertEqual(report.lab["lcp_ms"], 2100.0)
        self.assertEqual(report.field_data["interaction_to_next_paint"], 180.0)
        self.assertEqual(fake.fetch.call_args.kwargs["allowed_hosts"], pagespeed.HOSTS)

    @override_settings(
        SEO_GSC_ACCESS_TOKEN="tok", SEO_GSC_PROPERTY="https://example.org/"
    )
    def test_search_console_returns_only_real_rows_and_authenticates(self):
        from datetime import date

        from apps.seo.fetcher import FetchResult
        from apps.seo.integrations import search_console

        row = SEOSettings.get_solo()
        row.search_console_enabled = True
        row.save()
        body = {
            "rows": [
                {
                    "keys": ["https://example.org/a/"],
                    "clicks": 3,
                    "impressions": 90,
                    "ctr": 0.03,
                    "position": 4.2,
                }
            ]
        }
        fake = mock.Mock()
        fake.fetch.return_value = FetchResult(
            "u", "u", 200, {}, json.dumps(body).encode()
        )

        with mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake):
            rows = search_console.search_performance(
                date(2026, 1, 1), date(2026, 1, 31)
            )

        self.assertEqual(
            (rows[0].key, rows[0].clicks, rows[0].impressions),
            ("https://example.org/a/", 3.0, 90.0),
        )
        kwargs = fake.fetch.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["method"], "POST")
        self.assertFalse(kwargs["follow_redirects"])

        fake.fetch.return_value = FetchResult("u", "u", 403, {}, b"{}")
        with (
            mock.patch("apps.seo.fetcher.default_fetcher", return_value=fake),
            self.assertRaises(search_console.SearchConsoleError),
        ):
            search_console.search_performance(date(2026, 1, 1), date(2026, 1, 31))
