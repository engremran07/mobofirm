from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.contrib.sitemaps import Sitemap
from django.test import TestCase, override_settings

from apps.blog.models import Post
from apps.firmwares.models import AutomatedBlogLink, Brand, Model
from apps.seo import jobs, scan
from apps.seo.models import (
    CrawlRun,
    Issue,
    Metadata,
    PageSnapshot,
    SEOJob,
    SEOModel,
    SEOSettings,
)
from apps.seo.sitemap_registry import register_sitemap, unregister_sitemap
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_post

LONG_BODY = "<p>" + " ".join(["firmware"] * 200) + "</p>"


def open_issue(code: str, url: str) -> Issue | None:
    return Issue.objects.filter(code=code, url=url, status=Issue.Status.OPEN).first()


def codes_for(url: str) -> set[str]:
    return set(
        Issue.objects.filter(url=url, status=Issue.Status.OPEN).values_list(
            "code", flat=True
        )
    )


@override_settings(SITE_URL=SITE)
class ScanRunTests(TestCase):
    def test_scan_runs_without_http_and_records_review_fields(self):
        catalog = make_catalog()

        run = scan.run_scan()

        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertEqual(run.kind, CrawlRun.Kind.SCAN)
        self.assertFalse(run.partial)
        self.assertEqual(run.pages_crawled, run.urls_total)
        self.assertGreater(PageSnapshot.objects.count(), 8)
        snapshot = PageSnapshot.objects.get(path="/firmwares/nova/atlas-one/")
        self.assertEqual(snapshot.group, "models")
        self.assertEqual(snapshot.object_label, "firmwares.model")
        self.assertEqual(snapshot.object_pk, str(catalog.model.pk))
        self.assertTrue(snapshot.indexable)
        issue = open_issue("model_missing_flash_guide", "/firmwares/nova/atlas-one/")
        assert issue is not None
        self.assertEqual(issue.group, "models")
        self.assertEqual(issue.object_title, "Nova/Atlas One")

    def test_every_object_type_is_covered(self):
        make_catalog()
        scan.run_scan()
        groups = set(PageSnapshot.objects.values_list("group", flat=True))
        self.assertTrue(
            {"posts", "firmware", "models", "brands", "chipsets", "tools", "pages"}
            <= groups,
            groups,
        )

    def test_rescan_is_idempotent_and_does_not_duplicate(self):
        make_catalog()
        scan.run_scan()
        first = Issue.objects.count()
        fingerprints = set(Issue.objects.values_list("fingerprint", flat=True))

        second = scan.run_scan()

        self.assertEqual(Issue.objects.count(), first)
        self.assertEqual(
            set(Issue.objects.values_list("fingerprint", flat=True)), fingerprints
        )
        self.assertEqual(second.summary["resolved"], 0)
        self.assertEqual(
            Issue.objects.filter(status=Issue.Status.OPEN).count(),
            second.summary["open_after"],
        )

    def test_a_disabled_or_running_scan_is_refused(self):
        row = SEOSettings.get_solo()
        row.autonomy_mode = SEOSettings.AutonomyMode.OFF
        row.save()
        with self.assertRaises(scan.ScanUnavailable):
            scan.run_scan()
        row.autonomy_mode = SEOSettings.AutonomyMode.SUGGEST
        row.save()
        CrawlRun.objects.create(
            kind=CrawlRun.Kind.SCAN, status=CrawlRun.Status.RUNNING, base_url=SITE
        )
        with self.assertRaises(scan.ScanUnavailable):
            scan.run_scan()

    def test_time_budget_stops_the_scan_and_the_next_run_resumes(self):
        make_catalog()
        total = len(scan.collect_targets())
        ticks = iter(range(0, 100_000, 100))

        run = scan.run_scan(budget_seconds=150, clock=lambda: float(next(ticks)))

        self.assertTrue(run.partial)
        self.assertEqual(run.status, CrawlRun.Status.COMPLETED)
        self.assertGreater(run.cursor, 0)
        self.assertLess(run.cursor, total)
        first_cursor = run.cursor
        rounds = 0
        while run.partial and rounds < 80:
            run = scan.run_scan(budget_seconds=150, clock=lambda: float(next(ticks)))
            rounds += 1
        self.assertFalse(run.partial)
        self.assertEqual(run.cursor, 0)
        self.assertGreater(first_cursor, 0)
        self.assertEqual(
            PageSnapshot.objects.count(), scan.run_scan(resume=False).pages_crawled
        )

    def test_restart_ignores_the_cursor(self):
        make_catalog()
        ticks = iter(range(0, 100_000, 100))
        scan.run_scan(budget_seconds=150, clock=lambda: float(next(ticks)))

        fresh = scan.run_scan(resume=False)

        self.assertFalse(fresh.partial)
        self.assertEqual(fresh.pages_crawled, fresh.urls_total)

    def test_removed_pages_have_their_issues_resolved(self):
        catalog = make_catalog()
        scan.run_scan()
        url = "/pages/about/"
        self.assertIsNotNone(open_issue("page_thin_content", url))
        catalog.page.delete()

        scan.run_scan()

        self.assertIsNone(open_issue("page_thin_content", url))
        self.assertTrue(
            Issue.objects.filter(
                code="page_thin_content", url=url, status=Issue.Status.RESOLVED
            ).exists()
        )

    def test_ignored_issues_stay_ignored_when_they_reproduce(self):
        make_catalog()
        scan.run_scan()
        issue = open_issue("page_thin_content", "/pages/about/")
        assert issue is not None
        issue.status = Issue.Status.IGNORED
        issue.save()

        scan.run_scan()

        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.IGNORED)

    def test_sitemap_url_without_a_route_is_an_error(self):
        class Ghost(Sitemap):
            def items(self):
                return ["x"]

            def location(self, item):
                return "/no/such/route/"

        register_sitemap("ghost", Ghost)
        self.addCleanup(unregister_sitemap, "ghost")

        scan.run_scan()

        issue = open_issue("sitemap_url_unresolvable", "/no/such/route/")
        assert issue is not None
        self.assertEqual(issue.severity, Issue.Severity.ERROR)


@override_settings(SITE_URL=SITE)
class MetadataRuleTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.author = self.catalog.post.author
        self.category = self.catalog.category

    def test_title_length_rules_flag_and_clear(self):
        short = make_post("short", "Hi", self.author, self.category, seo_title="Hi")
        long = make_post(
            "long-title",
            "L",
            self.author,
            self.category,
            seo_title="A very long post title that keeps going well past sixty characters",
            body=LONG_BODY,
            summary="s" * 90,
        )
        ok = make_post(
            "ok-title",
            "Flash Atlas One safely",
            self.author,
            self.category,
            seo_title="Flash the Atlas One safely: a step-by-step guide",
            body=LONG_BODY,
            summary="s" * 90,
        )

        scan.run_scan()

        self.assertIn("title_too_short", codes_for(short.get_absolute_url()))
        long_issue = open_issue("title_too_long", long.get_absolute_url())
        assert long_issue is not None
        self.assertTrue(long_issue.fixable)
        self.assertLessEqual(len(long_issue.suggestion["value"]), 60)
        self.assertEqual(long_issue.seo_field, "meta_title")
        self.assertFalse(
            {"title_too_long", "title_too_short", "title_missing"}
            & codes_for(ok.get_absolute_url())
        )

    def test_generic_description_is_flagged_with_a_proposal_from_real_data(self):
        row = SEOSettings.get_solo()
        row.auto_meta_enabled = False
        row.save()
        Model.objects.filter(pk=self.catalog.model.pk).update(
            description="", model_code="NS-A1", os_version="Android 14"
        )

        scan.run_scan()

        issue = open_issue("description_missing", "/firmwares/nova/atlas-one/")
        assert issue is not None
        self.assertTrue(issue.fixable)
        value = issue.suggestion["value"]
        self.assertIn("Nova Atlas One", value)
        self.assertGreaterEqual(len(value), 70)
        self.assertLessEqual(len(value), 160)

    def test_description_with_no_data_to_build_a_proposal_is_not_fixable(self):
        row = SEOSettings.get_solo()
        row.auto_meta_enabled = False
        row.save()
        page = self.catalog.page
        page.seo_description = ""
        page.content = "<p>Short.</p>"
        page.save()

        scan.run_scan()

        issue = open_issue("description_missing", "/pages/about/")
        assert issue is not None
        self.assertFalse(issue.fixable)
        self.assertEqual(issue.suggestion, {})

    def test_thin_pages_are_info_and_not_noindex_warnings(self):
        Model.objects.create(
            brand=self.catalog.brand, name="Empty", slug="empty", description="x" * 90
        )

        scan.run_scan()

        thin = open_issue("thin_page", "/firmwares/nova/empty/")
        assert thin is not None
        self.assertEqual(thin.severity, Issue.Severity.INFO)
        self.assertIsNone(
            open_issue("noindex_public_content", "/firmwares/nova/empty/")
        )

    def test_override_noindex_is_fixable(self):
        page = self.catalog.page
        container = SEOModel.objects.create(
            content_type=ContentType.objects.get_for_model(page),
            object_id=str(page.pk),
        )
        Metadata.objects.create(seo=container, noindex=True)

        scan.run_scan()

        issue = open_issue("noindex_public_content", "/pages/about/")
        assert issue is not None
        self.assertTrue(issue.fixable)
        self.assertEqual(issue.seo_field, "noindex")

    def test_unclean_urls_are_reported_and_clean_ones_are_not(self):
        bad = make_post("Bad_Slug--x", "Bad slug post", self.author, self.category)

        scan.run_scan()

        self.assertIn("slug_quality", codes_for(bad.get_absolute_url()))
        self.assertNotIn("slug_quality", codes_for("/firmwares/nova/atlas-one/"))

    def test_duplicate_titles_are_flagged_on_every_page_and_clear_on_rescan(self):
        title = "Atlas One firmware flashing walkthrough and tips"
        first = make_post(
            "dup-one",
            "Same",
            self.author,
            self.category,
            seo_title=title,
            body=LONG_BODY,
            summary="a" * 90,
        )
        second = make_post(
            "dup-two",
            "Same",
            self.author,
            self.category,
            seo_title=title,
            body=LONG_BODY + "<p>extra</p>",
            summary="b" * 90,
        )

        scan.run_scan()

        for post in (first, second):
            self.assertIn("title_duplicate", codes_for(post.get_absolute_url()))
        Post.objects.filter(pk=second.pk).update(
            seo_title="Atlas One recovery steps when flashing fails badly"
        )

        scan.rescan_paths([second.get_absolute_url()])

        self.assertNotIn("title_duplicate", codes_for(first.get_absolute_url()))
        self.assertNotIn("title_duplicate", codes_for(second.get_absolute_url()))

    def test_social_image_and_schema_rules(self):
        scan.run_scan()
        self.assertIn("social_image_missing", codes_for("/firmwares/nova/atlas-one/"))
        row = SEOSettings.get_solo()
        row.default_social_image = "https://example.org/default.png"
        row.auto_schema_enabled = False
        row.save()

        scan.run_scan()

        self.assertNotIn(
            "social_image_missing", codes_for("/firmwares/nova/atlas-one/")
        )
        self.assertIn("schema_missing", codes_for("/firmwares/nova/atlas-one/"))


@override_settings(SITE_URL=SITE)
class ContentRuleTests(TestCase):
    def setUp(self):
        self.catalog = make_catalog()
        self.author = self.catalog.post.author
        self.category = self.catalog.category

    def test_post_content_checks(self):
        post = make_post(
            "messy",
            "A messy post about flashing",
            self.author,
            self.category,
            body="<p>placeholder</p>",
        )
        # Saved posts are sanitised; imported or AI-written bodies can still carry an <h1>.
        Post.objects.filter(pk=post.pk).update(
            body='<h1>Inside</h1><p>Short text.</p><img src="/a.png">'
            '<img src="/b.png" alt="B">'
        )

        scan.run_scan()

        codes = codes_for(post.get_absolute_url())
        self.assertTrue(
            {
                "post_thin_content",
                "post_body_h1",
                "post_no_summary",
                "post_images_missing_alt",
            }
            <= codes,
            codes,
        )

    def test_a_complete_post_has_no_content_issues(self):
        post = make_post(
            "complete",
            "A complete post about flashing",
            self.author,
            self.category,
            body=LONG_BODY,
            summary="A summary that is long enough to be a useful search snippet today.",
        )

        scan.run_scan()

        self.assertFalse(
            codes_for(post.get_absolute_url())
            & {
                "post_thin_content",
                "post_body_h1",
                "post_no_summary",
                "post_images_missing_alt",
                "duplicate_content",
            }
        )

    def test_duplicate_opening_text_is_flagged(self):
        a = make_post(
            "dup-a", "Dup A flashing", self.author, self.category, body=LONG_BODY
        )
        b = make_post(
            "dup-b", "Dup B flashing", self.author, self.category, body=LONG_BODY
        )

        scan.run_scan()

        for post in (a, b):
            self.assertIn("duplicate_content", codes_for(post.get_absolute_url()))

    def test_catalogue_objects_are_checked(self):
        Model.objects.filter(pk=self.catalog.model.pk).update(chipset_ref=None)
        self.catalog.tool.chipsets.clear()
        self.catalog.tool.supported_brands.clear()
        Brand.objects.filter(pk=self.catalog.brand.pk).update(description="")

        scan.run_scan()

        self.assertIn("model_no_chipset", codes_for("/firmwares/nova/atlas-one/"))
        self.assertIn("tool_unlinked", codes_for("/firmwares/tools/flasher/"))
        self.assertIn("brand_no_description", codes_for("/firmwares/nova/"))

    def test_chipset_without_a_tool_or_guide_is_flagged(self):
        self.catalog.tool.chipsets.clear()

        scan.run_scan()

        codes = codes_for("/firmwares/chipsets/mediatek-helio-g100/")
        self.assertIn("chipset_no_tool", codes)
        self.assertIn("chipset_missing_flash_guide", codes)

    def test_flash_guides_clear_the_guide_rules(self):
        make_post(
            "how-to-flash-nova-atlas-one",
            "How to flash the Nova Atlas One",
            self.author,
            self.category,
            body="<h3>How to flash firmware on Nova Atlas One</h3>" + LONG_BODY,
            firmware_model=self.catalog.model,
        )
        guide_chipset = make_post(
            "mediatek-helio-g100-chipset-firmware",
            "MediaTek Helio G100 firmware and tools",
            self.author,
            self.category,
            body="<h3>How to flash firmware on MediaTek Helio G100</h3>" + LONG_BODY,
        )
        AutomatedBlogLink.objects.create(
            kind="chipset", chipset=self.catalog.chipset, post=guide_chipset
        )

        scan.run_scan()

        self.assertNotIn(
            "model_missing_flash_guide", codes_for("/firmwares/nova/atlas-one/")
        )
        chipset_codes = codes_for("/firmwares/chipsets/mediatek-helio-g100/")
        self.assertNotIn("chipset_missing_flash_guide", chipset_codes)
        self.assertNotIn("chipset_no_tool", chipset_codes)

    def test_page_thin_content(self):
        scan.run_scan()
        self.assertIn("page_thin_content", codes_for("/pages/about/"))
        page = self.catalog.page
        page.content = LONG_BODY
        page.save()

        scan.run_scan()

        self.assertNotIn("page_thin_content", codes_for("/pages/about/"))


def _job(key: str, url: str, pk: str = "1", public: bool = False) -> SEOJob:
    return SEOJob.objects.create(
        kind="content_changed",
        job_key=key,
        payload={
            "label": "blog.post",
            "pk": pk,
            "url": url,
            "public": public,
            "previous_url": "",
            "previous_public": False,
        },
    )


@override_settings(SITE_URL=SITE)
class JobProcessingTests(TestCase):
    def test_process_queue_now_drains_jobs_without_celery(self):
        for index in range(7):
            _job(f"k{index}", f"/blog/p{index}/", str(index))

        counts = jobs.process_queue_now()

        self.assertEqual(counts["remaining"], 0)
        self.assertEqual(SEOJob.objects.filter(status=SEOJob.Status.PENDING).count(), 0)
        self.assertEqual(sum(v for k, v in counts.items() if k != "remaining"), 7)

    def test_process_queue_now_respects_its_time_budget(self):
        for index in range(3):
            _job(f"b{index}", "/blog/x/")

        counts = jobs.process_queue_now(budget_seconds=-1)

        self.assertEqual(counts["remaining"], 3)

    def test_content_change_jobs_rescan_the_changed_page(self):
        catalog = make_catalog()
        url = catalog.post.get_absolute_url()
        job = _job("rescan-1", url, str(catalog.post.pk), public=True)

        jobs.process_queue_now()

        job.refresh_from_db()
        self.assertEqual(job.result["rescan"]["scanned"], 1)
        self.assertTrue(PageSnapshot.objects.filter(path=url).exists())
