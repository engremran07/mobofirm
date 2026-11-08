from __future__ import annotations

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import Model
from apps.seo import fixes, ledger, scan
from apps.seo.models import (
    ChangeLedger,
    CrawlRun,
    Issue,
    Metadata,
    SEOJob,
    SEOSettings,
)
from apps.seo.proposals import fit_description, fit_title
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_post, make_staff

LONG_TITLE = "A very long post title that keeps going well past sixty characters"
URL_MODEL = "/firmwares/nova/atlas-one/"


def open_issue(code: str, url: str) -> Issue:
    issue = Issue.objects.filter(code=code, url=url, status=Issue.Status.OPEN).first()
    assert issue is not None, f"{code} {url} not open"
    return issue


def set_mode(mode: str) -> None:
    row = SEOSettings.get_solo()
    row.autonomy_mode = mode
    row.save()


class ProposalUnitTests(TestCase):
    def test_fit_title_keeps_the_site_name_when_it_fits_and_cuts_at_a_word(self):
        self.assertEqual(
            fit_title("Atlas One firmware", "Mobofirm", " | "),
            "Atlas One firmware | Mobofirm",
        )
        cut = fit_title(LONG_TITLE, "Mobofirm", " | ")
        self.assertLessEqual(len(cut), 60)
        self.assertTrue(LONG_TITLE.startswith(cut))
        self.assertFalse(cut.endswith(" "))
        self.assertEqual(fit_title("   ", "Mobofirm", " | "), "")

    def test_fit_description_cuts_at_a_sentence_or_word_boundary(self):
        text = "First sentence is here. " + "word " * 60
        value = fit_description(text)
        self.assertLessEqual(len(value), 160)
        self.assertEqual(fit_description("Short text."), "Short text.")
        sentence = fit_description("A" * 80 + ". " + "b " * 60)
        self.assertTrue(sentence.endswith("."))


@override_settings(SITE_URL=SITE)
class ApplyFixTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.user = make_staff("fixer", superuser=True)
        self.post = make_post(
            "long-title-post",
            "Short",
            self.catalog.post.author,
            self.catalog.category,
            seo_title=LONG_TITLE,
            body="<p>" + " ".join(["word"] * 200) + "</p>",
            summary="s" * 90,
        )
        self.url = self.post.get_absolute_url()
        scan.run_scan()
        self.issue = open_issue("title_too_long", self.url)

    def test_apply_writes_the_override_ledgers_it_and_closes_the_issue(self):
        entry = fixes.apply_fix(self.issue, self.user)

        self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)
        self.assertEqual(entry.target_model, "seo.metadata")
        self.assertEqual(entry.action, ChangeLedger.Action.CREATE)
        self.assertEqual(entry.kind, fixes.KIND_REWRITE)
        self.assertEqual(entry.decided_by, self.user)
        metadata = Metadata.objects.get(pk=entry.target_pk)
        self.assertEqual(metadata.meta_title, self.issue.suggestion["value"])
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.RESOLVED)
        self.assertEqual(self.issue.fix_ledger, entry)
        page = self.client.get(self.url)
        self.assertContains(page, f"<title>{metadata.meta_title}</title>", html=True)

    def test_an_edited_value_is_validated_against_the_standard(self):
        for bad in ("short", "x" * 61):
            with self.subTest(value=bad), self.assertRaises(fixes.FixError):
                fixes.apply_fix(self.issue, self.user, value=bad)
        self.assertEqual(ChangeLedger.objects.count(), 0)
        self.assertFalse(Metadata.objects.exists())

        entry = fixes.apply_fix(
            self.issue, self.user, value="Flash the Atlas One safely, step by step"
        )

        self.assertEqual(
            Metadata.objects.get(pk=entry.target_pk).meta_title,
            "Flash the Atlas One safely, step by step",
        )

    def test_a_second_apply_is_refused_without_a_second_ledger_row(self):
        fixes.apply_fix(self.issue, self.user)

        with self.assertRaises(fixes.FixError):
            fixes.apply_fix(self.issue, self.user)

        self.assertEqual(ChangeLedger.objects.count(), 1)

    def test_apply_is_refused_when_seo_automation_is_off(self):
        set_mode(SEOSettings.AutonomyMode.OFF)

        with self.assertRaises(fixes.FixError):
            fixes.apply_fix(self.issue, self.user)

        self.assertFalse(Metadata.objects.exists())

    def test_an_existing_override_is_updated_with_before_and_after(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.seo.models import SEOModel

        container = SEOModel.objects.create(
            content_type=ContentType.objects.get_for_model(self.post),
            object_id=str(self.post.pk),
        )
        Metadata.objects.create(
            seo=container, meta_title=LONG_TITLE, meta_description="d" * 80
        )

        entry = fixes.apply_fix(
            self.issue, self.user, value="Atlas One flashing guide for Nova"
        )

        self.assertEqual(entry.action, ChangeLedger.Action.UPDATE)
        self.assertEqual(entry.before, {"meta_title": LONG_TITLE})
        self.assertEqual(
            entry.after, {"meta_title": "Atlas One flashing guide for Nova"}
        )
        self.assertEqual(Metadata.objects.get(seo=container).meta_description, "d" * 80)

    def test_rollback_restores_the_state_and_reopens_the_issue(self):
        entry = fixes.apply_fix(self.issue, self.user)
        self.assertTrue(Metadata.objects.exists())

        ledger.rollback(entry, self.user)
        fixes.after_rollback(entry)

        self.assertFalse(Metadata.objects.exists())
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.OPEN)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.ROLLED_BACK)

    def test_ignore_needs_a_reason_and_can_be_restored(self):
        with self.assertRaises(fixes.FixError):
            fixes.ignore_issue(self.issue, self.user, " a ")

        fixes.ignore_issue(self.issue, self.user, "Brand voice, keep it long")
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.IGNORED)
        self.assertEqual(self.issue.ignored_reason, "Brand voice, keep it long")
        self.assertEqual(self.issue.ignored_by, self.user)
        with self.assertRaises(fixes.FixError):
            fixes.ignore_issue(self.issue, self.user, "twice is not allowed")

        fixes.restore_issue(self.issue)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.OPEN)
        with self.assertRaises(fixes.FixError):
            fixes.restore_issue(self.issue)

    def test_reaudit_closes_an_issue_fixed_at_the_source(self):
        from apps.blog.models import Post

        Post.objects.filter(pk=self.post.pk).update(
            seo_title="Flash the Atlas One safely: a guide"
        )

        outcome = fixes.reaudit_issue(self.issue)

        self.assertEqual(outcome["scanned"], 1)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.RESOLVED)

    def test_noindex_override_fix_removes_the_override(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.seo.models import SEOModel

        page = self.catalog.page
        container = SEOModel.objects.create(
            content_type=ContentType.objects.get_for_model(page),
            object_id=str(page.pk),
        )
        Metadata.objects.create(seo=container, noindex=True)
        scan.run_scan()
        issue = open_issue("noindex_public_content", "/pages/about/")

        fixes.apply_fix(issue, self.user, value="false")

        self.assertFalse(Metadata.objects.get(seo=container).noindex)
        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.RESOLVED)

    def test_an_issue_without_an_editable_field_cannot_be_applied(self):
        issue = open_issue("post_thin_content", "/blog/how-to-flash-the-atlas-one/")
        with self.assertRaises(fixes.FixError):
            fixes.apply_fix(issue, self.user, value="whatever it takes")


@override_settings(SITE_URL=SITE)
class BulkAndAutopilotTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.user = make_staff("bulker", superuser=True)
        row = SEOSettings.get_solo()
        row.auto_meta_enabled = False
        row.save()
        Model.objects.filter(pk=self.catalog.model.pk).update(
            description="", model_code="NS-A1", os_version="Android 14"
        )
        self.post = make_post(
            "long-title-post",
            "Short",
            self.catalog.post.author,
            self.catalog.category,
            seo_title=LONG_TITLE,
            body="<p>" + " ".join(["word"] * 200) + "</p>",
            summary="s" * 90,
        )

    def scan(self):
        return scan.run_scan()

    def test_safe_candidates_only_fill_missing_values(self):
        self.scan()

        candidates = fixes.safe_candidates(Issue.objects.all())

        codes = {issue.code for issue in candidates}
        self.assertEqual(codes, {"description_missing"})
        self.assertIn(URL_MODEL, {issue.url for issue in candidates})

    def test_apply_many_applies_each_and_reaudits_once(self):
        self.scan()
        candidates = fixes.safe_candidates(Issue.objects.all())

        result = fixes.apply_many(candidates, self.user, source="test")

        self.assertEqual(result.applied, len(candidates))
        self.assertEqual(result.failed, [])
        self.assertEqual(
            ChangeLedger.objects.filter(kind=fixes.KIND_ADD).count(), len(candidates)
        )
        self.assertFalse(fixes.safe_candidates(Issue.objects.all()))
        self.assertTrue(
            Issue.objects.filter(
                code="description_missing", status=Issue.Status.RESOLVED
            ).exists()
        )

    def test_suggest_mode_applies_nothing_after_a_scan(self):
        set_mode(SEOSettings.AutonomyMode.SUGGEST)

        self.scan()

        self.assertFalse(Metadata.objects.exists())
        self.assertTrue(
            Issue.objects.filter(
                code="description_missing", status=Issue.Status.OPEN
            ).exists()
        )

    def test_safe_autopilot_fills_missing_but_never_rewrites(self):
        set_mode(SEOSettings.AutonomyMode.SAFE_AUTOPILOT)

        self.scan()

        self.assertTrue(
            Metadata.objects.filter(seo__object_id=str(self.catalog.model.pk)).exists()
        )
        self.assertFalse(
            Metadata.objects.filter(seo__object_id=str(self.post.pk)).exists()
        )
        self.assertEqual(
            Issue.objects.get(
                code="title_too_long", url=self.post.get_absolute_url()
            ).status,
            Issue.Status.OPEN,
        )

    def test_full_mode_also_applies_rewrites(self):
        set_mode(SEOSettings.AutonomyMode.FULL)

        self.scan()

        self.assertTrue(
            Metadata.objects.filter(seo__object_id=str(self.post.pk)).exists()
        )
        self.assertEqual(
            Issue.objects.get(
                code="title_too_long", url=self.post.get_absolute_url()
            ).status,
            Issue.Status.RESOLVED,
        )


@override_settings(SITE_URL=SITE)
class ReviewScreenTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.admin = make_staff("boss", superuser=True)
        self.viewer = make_staff("viewer", "view_issue")
        self.post = make_post(
            "long-title-post",
            "Short",
            self.catalog.post.author,
            self.catalog.category,
            seo_title=LONG_TITLE,
            body="<p>" + " ".join(["word"] * 200) + "</p>",
            summary="s" * 90,
        )
        scan.run_scan()
        self.issue = open_issue("title_too_long", self.post.get_absolute_url())
        self.client.force_login(self.admin)

    def test_every_screen_renders_inside_the_admin_shell(self):
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
                self.assertContains(response, "mf-ui-header")
                self.assertContains(response, "SEO sections")
                self.assertContains(response, "mf-ui-tabs")
                self.assertNotContains(response, "mf-seo-")

    def test_board_lists_issues_by_object_type_with_counts(self):
        response = self.client.get(reverse("seo:issues"))

        self.assertContains(response, "Object types")
        self.assertContains(response, "Posts")
        self.assertContains(response, "Models")
        self.assertContains(response, "Title is too long")
        self.assertContains(response, "Review and fix")

        only_models = self.client.get(reverse("seo:issues"), {"group": "models"})
        self.assertContains(only_models, "guide for this model")
        self.assertEqual(
            {row["issue"].group for row in only_models.context["rows"]}, {"models"}
        )

    def test_board_filters_by_severity_code_search_and_fixable(self):
        by_code = self.client.get(reverse("seo:issues"), {"code": "title_too_long"})
        self.assertContains(by_code, LONG_TITLE[:30])
        self.assertNotContains(by_code, "No flash guide")
        found = self.client.get(reverse("seo:issues"), {"q": "long-title-post"})
        self.assertContains(found, "Title is too long")
        fixable = self.client.get(reverse("seo:issues"), {"fixable": "1"})
        self.assertNotContains(fixable, "post is too thin")
        warnings = self.client.get(reverse("seo:issues"), {"severity": "warning"})
        self.assertContains(warnings, "mf-ui-badge--warning")
        self.assertNotContains(warnings, "mf-ui-badge--danger")

    def test_board_paginates(self):
        for index in range(30):
            Issue.objects.create(
                code="custom",
                severity="info",
                url=f"/x/{index}/",
                message=f"extra {index}",
                fingerprint=f"extra-{index}",
                group="pages",
            )

        first = self.client.get(reverse("seo:issues"), {"group": "pages"})
        second = self.client.get(reverse("seo:issues"), {"group": "pages", "page": "2"})

        self.assertContains(first, "Page 1 of")
        self.assertContains(second, "Page 2 of")

    def test_htmx_loads_the_fix_panel_with_the_proposal(self):
        response = self.client.get(
            reverse("seo:issue_detail", args=[self.issue.pk]), HTTP_HX_REQUEST="true"
        )

        self.assertNotContains(response, "<html")
        self.assertContains(response, "<textarea")
        self.assertContains(response, self.issue.suggestion["value"])
        self.assertContains(response, "Apply fix")
        full = self.client.get(reverse("seo:issue_detail", args=[self.issue.pk]))
        self.assertContains(full, "mf-ui-header")
        self.assertContains(full, "Apply fix")

    def test_applying_from_the_board_redirects_and_the_issue_disappears(self):
        board = reverse("seo:issues")
        response = self.client.post(
            reverse("seo:issue_apply", args=[self.issue.pk]),
            {"value": "Flash the Atlas One safely, step by step", "next": board},
        )

        self.assertRedirects(response, board)
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.RESOLVED)
        followed = self.client.get(board)
        self.assertNotContains(followed, LONG_TITLE[:40])
        self.assertTrue(
            ChangeLedger.objects.filter(
                status=ChangeLedger.Status.APPLIED, kind=fixes.KIND_REWRITE
            ).exists()
        )

    def test_an_invalid_value_returns_to_the_panel_with_an_error(self):
        response = self.client.post(
            reverse("seo:issue_apply", args=[self.issue.pk]),
            {"value": "no", "next": reverse("seo:issues")},
            follow=True,
        )

        self.assertContains(response, "Title needs at least")
        self.assertContains(response, "Apply fix")
        self.assertFalse(Metadata.objects.exists())

    def test_ignore_restore_and_reaudit_endpoints(self):
        self.client.post(
            reverse("seo:issue_ignore", args=[self.issue.pk]), {"reason": "Intentional"}
        )
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.IGNORED)
        ignored = self.client.get(reverse("seo:issues"), {"status": "ignored"})
        self.assertContains(ignored, "Ignored: Intentional")
        self.client.post(reverse("seo:issue_restore", args=[self.issue.pk]))
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.OPEN)
        response = self.client.post(
            reverse("seo:issue_reaudit", args=[self.issue.pk]), follow=True
        )
        self.assertContains(response, "still stands")

    def test_bulk_preview_then_apply(self):
        row = SEOSettings.get_solo()
        row.auto_meta_enabled = False
        row.save()
        Model.objects.filter(pk=self.catalog.model.pk).update(
            description="", model_code="NS-A1"
        )
        scan.run_scan()
        url = reverse("seo:issues_bulk")

        preview = self.client.get(url, {"fixable": "1"})
        self.assertContains(preview, "will be filled")
        self.assertContains(preview, "Nova Atlas One")
        self.assertFalse(Metadata.objects.exists())

        response = self.client.post(url, {"fixable": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Metadata.objects.exists())
        self.assertFalse(fixes.safe_candidates(Issue.objects.all()))
        empty = self.client.get(url)
        self.assertContains(empty, "nothing safe to apply")

    def test_run_scan_endpoint_runs_synchronously_and_reports(self):
        before = CrawlRun.objects.filter(kind=CrawlRun.Kind.SCAN).count()

        response = self.client.post(reverse("seo:run_scan"), follow=True)

        self.assertEqual(
            CrawlRun.objects.filter(kind=CrawlRun.Kind.SCAN).count(), before + 1
        )
        self.assertContains(response, "URLs checked")

    def test_run_scan_reports_when_automation_is_off(self):
        set_mode(SEOSettings.AutonomyMode.OFF)

        response = self.client.post(reverse("seo:run_scan"), follow=True)

        self.assertContains(response, "switched off")

    def test_process_queue_now_endpoint_drains_the_queue(self):
        for index in range(30):
            SEOJob.objects.create(
                kind="content_changed",
                job_key=f"queue-{index}",
                payload={
                    "label": "blog.post",
                    "pk": str(index),
                    "url": f"/blog/q{index}/",
                    "public": False,
                    "previous_url": "",
                    "previous_public": False,
                },
            )

        response = self.client.post(reverse("seo:process_jobs"), follow=True)

        self.assertContains(response, "The queue is empty")
        self.assertEqual(SEOJob.objects.filter(status=SEOJob.Status.PENDING).count(), 0)

    def test_dashboard_reports_the_scan_and_group_counts(self):
        response = self.client.get(reverse("seo:dashboard"))

        self.assertContains(response, "Run scan now")
        self.assertContains(response, "Open issues by object type")
        self.assertContains(response, "Last content scan")
        self.assertContains(response, "Fixes applied")

    def test_inventory_lists_scanned_pages_and_filters(self):
        response = self.client.get(reverse("seo:inventory"), {"group": "models"})

        paths = [row["snapshot"].path for row in response.context["rows"]]
        self.assertIn(URL_MODEL, paths)
        self.assertNotIn("/pages/about/", paths)

    def test_inspector_links_to_issue_fixes(self):
        response = self.client.get(
            reverse("seo:inspect"), {"url": self.post.get_absolute_url()}
        )

        self.assertContains(response, "Review and fix")

    def test_rollback_from_the_ledger_reopens_the_issue(self):
        fixes.apply_fix(self.issue, self.admin)
        entry = ChangeLedger.objects.get()

        self.client.post(reverse("seo:ledger_action", args=[entry.pk, "rollback"]))

        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.OPEN)
        self.assertFalse(Metadata.objects.exists())


@override_settings(SITE_URL=SITE)
class ReviewPermissionTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        scan.run_scan()
        issue = Issue.objects.filter(code="description_too_short").first()
        assert issue is not None
        self.issue: Issue = issue

    def test_viewers_can_read_but_not_change_anything(self):
        viewer = make_staff("viewer", "view_issue")
        self.client.force_login(viewer)

        self.assertEqual(self.client.get(reverse("seo:issues")).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("seo:issue_detail", args=[self.issue.pk])
            ).status_code,
            200,
        )
        for name, args in (
            ("issue_apply", [self.issue.pk]),
            ("issue_ignore", [self.issue.pk]),
            ("issue_restore", [self.issue.pk]),
            ("issue_reaudit", [self.issue.pk]),
            ("issues_bulk", []),
            ("run_scan", []),
            ("process_jobs", []),
        ):
            with self.subTest(endpoint=name):
                self.assertEqual(
                    self.client.post(reverse(f"seo:{name}", args=args)).status_code, 403
                )
        self.issue.refresh_from_db()
        self.assertEqual(self.issue.status, Issue.Status.OPEN)

    def test_state_changing_endpoints_are_post_only(self):
        self.client.force_login(make_staff("boss", superuser=True))
        for name in ("issue_apply", "issue_ignore", "issue_restore", "issue_reaudit"):
            with self.subTest(endpoint=name):
                url = reverse(f"seo:{name}", args=[self.issue.pk])
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.get(reverse("seo:run_scan")).status_code, 405)

    def test_anonymous_and_regular_users_are_redirected(self):
        for name, args in (
            ("issues", []),
            ("issue_detail", [self.issue.pk]),
            ("issues_bulk", []),
        ):
            with self.subTest(endpoint=name):
                self.assertEqual(
                    self.client.get(reverse(f"seo:{name}", args=args)).status_code, 302
                )

    def test_csrf_is_enforced_on_fix_actions(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(make_staff("boss2", superuser=True))
        for name in ("issue_apply", "issue_ignore", "run_scan"):
            args = [] if name == "run_scan" else [self.issue.pk]
            with self.subTest(endpoint=name):
                self.assertEqual(
                    strict.post(reverse(f"seo:{name}", args=args)).status_code, 403
                )

    def test_the_fix_panel_hides_apply_without_the_metadata_permission(self):
        self.client.force_login(make_staff("viewer", "view_issue"))

        response = self.client.get(
            reverse("seo:issue_detail", args=[self.issue.pk]), HTTP_HX_REQUEST="true"
        )

        self.assertNotContains(response, "Apply fix")

    def test_unsafe_next_urls_are_ignored(self):
        self.client.force_login(make_staff("boss3", superuser=True))

        response = self.client.post(
            reverse("seo:issue_ignore", args=[self.issue.pk]),
            {"reason": "Because", "next": "https://evil.example/"},
        )

        self.assertRedirects(response, reverse("seo:issues"))
