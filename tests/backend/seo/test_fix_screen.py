from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.blog.models import Post
from apps.firmwares.models import Model
from apps.seo import editable, jobs, ledger, scan
from apps.seo.fetcher import FetchError
from apps.seo.models import ChangeLedger, Issue, Metadata, SEOJob, SEOSettings
from apps.seo.policy import route_ref
from apps.seo.rules import ALL_RULES
from tests.backend.seo.seo_helpers import (
    SITE,
    Catalog,
    make_catalog,
    make_staff,
)

HOSTS = ["example.org", "testserver", "localhost"]
TITLE = "Nova Atlas One firmware download and flashing"
DESCRIPTION = (
    "Download the stock firmware for the Nova Atlas One and follow the flashing "
    "guide with the supported tools."
)


def object_paths(data: Catalog) -> dict[str, str]:
    return {
        "blog.post": data.post.get_absolute_url(),
        "pages.page": reverse("pages:page", args=["about"]),
        "tags.tag": reverse("tags:detail", args=["flashing"]),
        "firmwares.brand": reverse("firmwares:brand_detail", args=["nova"]),
        "firmwares.model": reverse(
            "firmwares:model_detail", args=["nova", "atlas-one"]
        ),
        "firmwares.chipset": reverse(
            "firmwares:chipset_detail", args=["mediatek-helio-g100"]
        ),
        "firmwares.flashingtool": reverse(
            "firmwares:flashing_tool_detail", args=["flasher"]
        ),
        "firmwares.officialfirmware": reverse(
            "firmwares:firmware_detail", args=["official", data.firmware.pk]
        ),
        "seo.seosettings": reverse("firmwares:brand_list"),
    }


class FixTestCase(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def setUp(self):
        self.admin = make_staff("boss", superuser=True)
        self.client.force_login(self.admin)

    def screen(self, path: str):
        response = self.client.get(reverse("seo:fix"), {"url": path})
        self.assertEqual(response.status_code, 200, path)
        return response.context["screen"]

    def form(self, path: str, **values: Any) -> dict[str, Any]:
        """The posted form the screen would send, with ``values`` typed in."""
        screen = self.screen(path)
        form: dict[str, Any] = {"url": path, "next": reverse("seo:issues")}
        for field in screen.fields:
            form[field.post_name] = field.value
            form[field.original_name] = field.original
        for image in screen.alt_inputs:
            form[f"altsrc_{image.position}"] = image.src
        form.update(values)
        return form

    def save(self, path: str, action: str = "save", **values: Any):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("seo:fix_apply"),
                {**self.form(path, **values), "action": action},
            )


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class EveryObjectTypeIsFixableTests(FixTestCase):
    def test_every_object_type_opens_the_screen_with_its_owner(self):
        for label, path in object_paths(self.data).items():
            with self.subTest(label=label):
                screen = self.screen(path)
                self.assertEqual(screen.label, label)
                self.assertTrue(screen.pk)
                self.assertEqual(screen.is_route, label == "seo.seosettings")
                self.assertTrue(screen.fields)
                self.assertTrue(screen.page.headings or screen.page.error)

    def test_meta_title_and_description_save_for_every_object_type(self):
        for label, path in object_paths(self.data).items():
            with self.subTest(label=label):
                response = self.save(
                    path, f_meta_title=TITLE, f_meta_description=DESCRIPTION
                )

                self.assertEqual(response.status_code, 302)
                head = self.client.get(path).content.decode()
                self.assertIn(f"<title>{TITLE}</title>", head)
                self.assertIn(DESCRIPTION, head)
                screen = self.screen(path)
                stored = Metadata.objects.get(
                    seo__content_type__app_label=screen.label.split(".")[0],
                    seo__content_type__model=screen.label.split(".")[1],
                    seo__object_id=screen.pk,
                )
                self.assertEqual(stored.meta_title, TITLE)

    def test_a_page_without_an_object_is_owned_by_its_route(self):
        path = reverse("firmwares:brand_list")

        screen = self.screen(path)

        self.assertEqual((screen.label, screen.pk), route_ref(path))
        self.assertTrue(screen.is_route)
        self.assertEqual(screen.admin_url, "")

    def test_feeds_index_is_covered_and_fixable(self):
        path = reverse("feeds:index")

        screen = self.screen(path)

        self.assertEqual(screen.group_label, "Pages & tags")
        self.save(path, f_meta_title=TITLE)
        self.assertIn(TITLE, self.client.get(path).content.decode())

    def test_private_and_unknown_urls_are_not_fixable(self):
        for path in ("/admin/", "/accounts/login/", "/no/such/page/"):
            with self.subTest(path=path):
                response = self.client.get(reverse("seo:fix"), {"url": path})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(reverse("seo:fix"), {"url": "//evil"}).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse("seo:fix"), {"url": "x"}).status_code, 404
        )

    def test_every_scanned_url_resolves_to_an_owner(self):
        scan.run_scan()

        orphans = Issue.objects.filter(
            status=Issue.Status.OPEN, object_label=""
        ).exclude(url="")

        self.assertFalse(orphans.exists(), list(orphans.values_list("code", "url")))
        for issue in Issue.objects.filter(status=Issue.Status.OPEN).exclude(url="")[
            :15
        ]:
            self.assertEqual(
                self.client.get(reverse("seo:fix"), {"issue": issue.pk}).status_code,
                200,
                issue.code,
            )


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class ObjectFieldFixTests(FixTestCase):
    def test_slug_edits_every_observed_owner_and_queues_republish_with_the_old_url(
        self,
    ):
        skipped = {
            "seo.seosettings",
            "firmwares.officialfirmware",
            "firmwares.flashingtool",
            "firmwares.brand",
        }
        for label, path in object_paths(self.data).items():
            if label in skipped:
                continue
            with self.subTest(label=label):
                screen = self.screen(path)
                obj = editable.load(label, screen.pk)
                new_slug = f"{obj.slug}-renamed"

                response = self.save(path, f_slug=new_slug)

                self.assertEqual(response.status_code, 302, label)
                obj.refresh_from_db()
                self.assertEqual(obj.slug, new_slug)
                entry = ChangeLedger.objects.filter(
                    target_model=label, kind="object_fix"
                ).latest("pk")
                self.assertEqual(entry.after, {"slug": new_slug})
                self.assertEqual(entry.status, ChangeLedger.Status.APPLIED)
                job = SEOJob.objects.filter(
                    payload__label=label, payload__previous_url=path
                ).first()
                self.assertIsNotNone(job, label)

    def test_a_slug_is_read_only_where_no_redirect_machinery_watches_the_model(self):
        screen = self.screen(object_paths(self.data)["firmwares.flashingtool"])

        names = {field.name for field in screen.fields}

        self.assertNotIn("slug", names)
        self.assertIn("description", names)

    def test_a_moved_url_gets_a_redirect_proposal_through_the_job_outbox(self):
        path = self.data.post.get_absolute_url()

        self.save(path, f_slug="a-better-url")
        jobs.process_pending()

        self.assertTrue(
            ChangeLedger.objects.filter(
                kind__startswith="redirect", after__source=path
            ).exists()
        )

    def test_invalid_slug_and_duplicate_slug_are_rejected_and_nothing_is_saved(self):
        path = reverse("firmwares:brand_detail", args=["nova"])
        other = type(self.data.brand).objects.create(name="Other", slug="other")

        for bad in ("Not A Slug", "double--hyphen", other.slug, "x" * 90):
            with self.subTest(slug=bad):
                response = self.save(path, f_slug=bad, f_meta_title=TITLE)

                self.assertEqual(response.status_code, 302)
                self.data.brand.refresh_from_db()
                self.assertEqual(self.data.brand.slug, "nova")
                self.assertFalse(Metadata.objects.exists())
                self.assertFalse(
                    ChangeLedger.objects.filter(kind__in=["object_fix"]).exists()
                )

    def test_summary_and_brand_description_are_editable(self):
        self.save(self.data.post.get_absolute_url(), f_summary="A new short summary.")
        self.save(
            reverse("firmwares:brand_detail", args=["nova"]),
            f_description="Nova makes phones and tablets.",
        )

        self.data.post.refresh_from_db()
        self.data.brand.refresh_from_db()
        self.assertEqual(self.data.post.summary, "A new short summary.")
        self.assertEqual(self.data.brand.description, "Nova makes phones and tablets.")

    def test_alt_text_and_body_headings_are_repaired_in_the_body(self):
        Post.objects.filter(pk=self.data.post.pk).update(
            body='<p>Intro text.</p><h1>Wrong heading</h1><img src="/media/a.png">'
            '<img src="/media/b.png" alt="kept">'
        )
        path = self.data.post.get_absolute_url()
        screen = self.screen(path)
        self.assertEqual([image.src for image in screen.alt_inputs], ["/media/a.png"])
        self.assertEqual(screen.h1_in_body, 1)

        self.save(path, alt_0='Flash tool "Odin" on screen', demote_h1="1")

        self.data.post.refresh_from_db()
        self.assertIn(
            'alt="Flash tool &quot;Odin&quot; on screen"', self.data.post.body
        )
        self.assertIn('alt="kept"', self.data.post.body)
        self.assertIn("<h2>Wrong heading</h2>", self.data.post.body)
        self.assertNotIn("<h1", self.data.post.body)
        self.assertEqual(self.screen(path).alt_inputs, [])

    def test_stale_alt_form_is_refused_when_the_images_changed(self):
        Post.objects.filter(pk=self.data.post.pk).update(
            body='<p>Intro.</p><img src="/media/a.png">'
        )
        path = self.data.post.get_absolute_url()
        form = self.form(path, alt_0="Alt text")
        Post.objects.filter(pk=self.data.post.pk).update(
            body='<p>Intro.</p><img src="/media/other.png">'
        )

        self.client.post(reverse("seo:fix_apply"), {**form, "action": "save"})

        self.data.post.refresh_from_db()
        self.assertNotIn("Alt text", self.data.post.body)


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class LedgerAndSafetyTests(FixTestCase):
    def test_rollback_restores_overrides_and_object_fields(self):
        path = reverse("firmwares:brand_detail", args=["nova"])
        self.save(path, f_meta_title=TITLE, f_slug="nova-phones")
        entries = list(ChangeLedger.objects.order_by("pk"))
        self.assertEqual(
            {entry.kind for entry in entries}, {"metadata_fix_add", "object_fix"}
        )

        for entry in reversed(entries):
            ledger.rollback(entry, self.admin)

        self.data.brand.refresh_from_db()
        self.assertEqual(self.data.brand.slug, "nova")
        self.assertFalse(Metadata.objects.exists())
        self.assertEqual(
            {entry.status for entry in ChangeLedger.objects.all()},
            {ChangeLedger.Status.ROLLED_BACK},
        )

    def test_repeating_a_submission_writes_nothing_twice(self):
        path = self.data.post.get_absolute_url()
        stale = self.form(path, f_meta_title=TITLE)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("seo:fix_apply"), {**stale, "action": "save"})
        count = ChangeLedger.objects.count()
        jobs_count = SEOJob.objects.count()

        with self.captureOnCommitCallbacks(execute=True):
            again = self.client.post(
                reverse("seo:fix_apply"), {**stale, "action": "save"}
            )
        fresh = self.client.post(
            reverse("seo:fix_apply"), {**self.form(path), "action": "save"}
        )

        self.assertEqual(again.status_code, 302)
        self.assertEqual(fresh.status_code, 302)
        self.assertEqual(ChangeLedger.objects.count(), count)
        self.assertEqual(SEOJob.objects.count(), jobs_count)
        self.assertEqual(Metadata.objects.count(), 1)

    def test_a_save_republishes_through_the_job_outbox(self):
        path = self.data.post.get_absolute_url()

        self.save(path, f_meta_title=TITLE)

        job = SEOJob.objects.get(kind=jobs.CONTENT_CHANGED)
        self.assertEqual(job.payload["label"], "blog.post")
        self.assertEqual(job.payload["url"], path)
        self.assertTrue(job.payload["public"])

    def test_a_save_reaudits_the_page_and_closes_the_fixed_issue(self):
        Post.objects.filter(pk=self.data.post.pk).update(
            seo_title="Complete step by step flashing walkthrough for every Atlas One build"
        )
        scan.run_scan()
        path = self.data.post.get_absolute_url()
        issue = Issue.objects.get(url=path, code="title_too_long")

        self.save(path, f_meta_title=TITLE)

        issue.refresh_from_db()
        self.assertEqual(issue.status, Issue.Status.RESOLVED)
        self.assertIsNotNone(issue.fix_ledger)

    def test_post_redirect_get_for_browsers_and_hx_redirect_for_htmx(self):
        path = self.data.post.get_absolute_url()

        browser = self.client.post(
            reverse("seo:fix_apply"),
            {**self.form(path, f_meta_title=TITLE), "action": "save"},
        )
        htmx = self.client.post(
            reverse("seo:fix_apply"),
            {**self.form(path, f_meta_title=f"{TITLE} two"), "action": "save"},
            headers={"HX-Request": "true"},
        )
        rejected = self.client.post(
            reverse("seo:fix_apply"),
            {**self.form(path, f_meta_title="short"), "action": "save"},
        )

        self.assertEqual(browser.status_code, 302)
        self.assertIn(reverse("seo:fix"), browser["Location"])
        self.assertEqual(htmx.status_code, 204)
        self.assertIn(reverse("seo:fix"), htmx["HX-Redirect"])
        self.assertEqual(rejected.status_code, 302)
        self.assertNotIn("short", Metadata.objects.get().meta_title)

    def test_a_rejected_form_keeps_what_was_typed(self):
        path = self.data.post.get_absolute_url()
        self.client.post(
            reverse("seo:fix_apply"),
            {**self.form(path, f_meta_title="short"), "action": "save"},
        )

        screen = self.client.get(reverse("seo:fix"), {"url": path}).context["screen"]

        values = {field.name: field.value for field in screen.fields}
        self.assertEqual(values["meta_title"], "short")
        again = self.client.get(reverse("seo:fix"), {"url": path}).context["screen"]
        self.assertNotEqual(
            {f.name: f.value for f in again.fields}["meta_title"], "short"
        )

    def test_invalid_override_values_are_rejected_atomically(self):
        path = self.data.post.get_absolute_url()

        for field, value in (
            ("f_canonical_url", "javascript:alert(1)"),
            ("f_robots_directives", "index, bogus"),
            ("f_social_image", "/relative.png"),
            ("f_meta_description", "too short"),
        ):
            with self.subTest(field=field):
                self.save(path, **{field: value, "f_meta_title": TITLE})
                self.assertFalse(Metadata.objects.exists())
                self.assertFalse(ChangeLedger.objects.exists())

    def test_canonical_robots_noindex_and_social_overrides_apply(self):
        path = self.data.post.get_absolute_url()

        self.save(
            path,
            f_canonical_url="https://example.org/blog/elsewhere/",
            f_social_title="Social title",
            f_social_image="https://example.org/media/card.png",
            f_noindex="true",
        )

        metadata = Metadata.objects.get()
        self.assertEqual(metadata.canonical_url, "https://example.org/blog/elsewhere/")
        self.assertEqual(metadata.social_title, "Social title")
        self.assertTrue(metadata.noindex)
        head = self.client.get(path).content.decode()
        self.assertIn('content="noindex', head)

    def test_automation_off_blocks_saving(self):
        row = SEOSettings.get_solo()
        row.autonomy_mode = SEOSettings.AutonomyMode.OFF
        row.save()

        self.save(self.data.post.get_absolute_url(), f_meta_title=TITLE)

        self.assertFalse(Metadata.objects.exists())

    def test_the_serp_preview_endpoint_flags_cut_off_text(self):
        path = self.data.post.get_absolute_url()

        response = self.client.post(
            reverse("seo:fix_preview"),
            {"url": path, "f_meta_title": "T" * 80, "f_meta_description": "d"},
            headers={"HX-Request": "true"},
        )

        self.assertContains(response, "Title is cut off in results")
        self.assertContains(response, "example.org")

    def test_save_and_next_moves_to_the_next_open_issue(self):
        scan.run_scan()
        first = Issue.objects.filter(status=Issue.Status.OPEN).exclude(url="").first()
        assert first is not None
        form = self.form(first.url)

        response = self.client.post(
            reverse("seo:fix_apply"), {**form, "action": "next"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("seo:fix"), response["Location"])
        self.assertNotIn(f"url={first.url}", response["Location"])

    def test_reaudit_only_changes_nothing(self):
        path = self.data.post.get_absolute_url()

        response = self.client.post(
            reverse("seo:fix_apply"), {"url": path, "action": "reaudit"}, follow=True
        )

        self.assertContains(response, "Re-audited")
        self.assertFalse(ChangeLedger.objects.exists())


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class FindingsInContextTests(FixTestCase):
    def test_every_rule_code_opens_a_screen_with_an_inline_fix_path(self):
        path = object_paths(self.data)["firmwares.brand"]
        for code in ALL_RULES:
            with self.subTest(code=code):
                Issue.objects.all().delete()
                issue = Issue.objects.create(
                    code=code,
                    severity="warning",
                    url=path,
                    message=f"{code} finding",
                    fingerprint=f"fp-{code}",
                    group="brands",
                    object_label="firmwares.brand",
                    object_pk=str(self.data.brand.pk),
                    object_title="Nova",
                )

                response = self.client.get(reverse("seo:fix"), {"issue": issue.pk})

                self.assertEqual(response.status_code, 200)
                screen = response.context["screen"]
                self.assertTrue(screen.fields, code)
                self.assertContains(response, f"{code} finding")
                self.assertContains(response, "Save and re-audit")
                self.assertTrue(
                    screen.flagged
                    or screen.alt_flagged
                    or screen.h1_flagged
                    or screen.console_links
                    or screen.guides_url
                    or screen.other,
                    code,
                )

    def test_findings_highlight_their_own_fields_and_repairs(self):
        Post.objects.filter(pk=self.data.post.pk).update(
            summary="", body='<h1>Body title</h1><p>Short.</p><img src="/media/x.png">'
        )
        scan.run_scan()
        path = self.data.post.get_absolute_url()

        screen = self.screen(path)

        flagged = {field.name for field in screen.flagged}
        self.assertIn("summary", flagged)
        self.assertTrue(screen.alt_flagged and screen.alt_inputs)
        self.assertTrue(screen.h1_flagged and screen.h1_in_body)
        self.assertTrue(any(issue.code == "post_no_summary" for issue in screen.issues))
        response = self.client.get(reverse("seo:fix"), {"url": path})
        self.assertContains(response, "Finding")
        self.assertContains(response, "Heading outline")
        self.assertContains(response, "Search result preview")

    def test_source_findings_open_the_console_form_at_the_field(self):
        bare = Model.objects.create(
            brand=self.data.brand, name="Bare", slug="bare", model_code="NS-B1"
        )
        scan.run_scan()
        path = reverse("firmwares:model_detail", args=["nova", "bare"])

        screen = self.screen(path)

        self.assertTrue(
            any(issue.code == "model_no_chipset" for issue in screen.issues)
        )
        self.assertIn(
            f"{reverse('admin:firmwares_model_change', args=[bare.pk])}#id_chipset_ref",
            [link.url for link in screen.console_links],
        )

    def test_flash_guide_findings_link_to_the_guides_screen(self):
        Post.objects.all().delete()
        scan.run_scan()
        path = object_paths(self.data)["firmwares.model"]

        screen = self.screen(path)

        self.assertTrue(
            any(issue.code == "model_missing_flash_guide" for issue in screen.issues)
        )
        self.assertEqual(screen.guides_url, reverse("seo:guides"))

    def test_the_screen_uses_no_browser_native_dialogs(self):
        path = self.data.post.get_absolute_url()

        html = self.client.get(reverse("seo:fix"), {"url": path}).content.decode()

        for forbidden in ("confirm(", "alert(", "prompt(", "hx-confirm", "onclick"):
            self.assertNotIn(forbidden, html)

    def test_the_feeds_index_is_listed_in_the_static_sitemap(self):
        listed = self.client.get("/sitemap-static-1.xml").content.decode()

        self.assertIn(f"{SITE}{reverse('feeds:index')}", listed)

    def test_unreadable_pages_do_not_break_the_screen(self):
        with patch(
            "apps.seo.analysis.InProcessFetcher.fetch", side_effect=FetchError("down")
        ):
            response = self.client.get(
                reverse("seo:fix"), {"url": object_paths(self.data)["blog.post"]}
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "down")


@override_settings(SITE_URL=SITE, ALLOWED_HOSTS=HOSTS)
class PermissionMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()
        call_command("bootstrap_site", verbosity=0)
        user_model = get_user_model()

        def member(name: str, group: str | None):
            user = user_model.objects.create_user(
                username=name, email=f"{name}@example.com", password="pass-12345-x"
            )
            user.is_staff = True
            user.save()
            if group:
                user.groups.add(Group.objects.get(name=group))
            return user

        cls.moderator = member("mod", "Moderators")
        cls.editor = member("ed", "Editors")
        cls.plain = member("plain", None)
        cls.root = make_staff("root", superuser=True)
        cls.path = cls.data.post.get_absolute_url()

    def post(self, user=None):
        if user is not None:
            self.client.force_login(user)
        form = {
            "url": self.path,
            "action": "save",
            "f_meta_title": TITLE,
            "o_meta_title": "",
        }
        return self.client.post(reverse("seo:fix_apply"), form)

    def test_administrators_moderators_and_editors_can_fix(self):
        for user in (self.root, self.moderator, self.editor):
            with self.subTest(user=user.username):
                Metadata.objects.all().delete()
                response = self.post(user)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(Metadata.objects.filter(meta_title=TITLE).exists())

    def test_staff_without_the_permission_can_read_but_not_save(self):
        self.client.force_login(self.plain)

        page = self.client.get(reverse("seo:fix"), {"url": self.path})
        saved = self.post()

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "change metadata")
        self.assertEqual(saved.status_code, 403)
        self.assertFalse(Metadata.objects.exists())

    def test_anonymous_visitors_are_sent_to_login(self):
        self.client.logout()

        read = self.client.get(reverse("seo:fix"), {"url": self.path})
        write = self.client.post(reverse("seo:fix_apply"), {"url": self.path})

        self.assertEqual(read.status_code, 302)
        self.assertEqual(write.status_code, 302)
        self.assertIn("login", read["Location"])
        self.assertFalse(Metadata.objects.exists())

    def test_non_staff_users_cannot_reach_the_screen(self):
        user = get_user_model().objects.create_user(
            username="visitor", email="v@example.com", password="pass-12345-x"
        )
        self.client.force_login(user)

        self.assertEqual(
            self.client.get(reverse("seo:fix"), {"url": self.path}).status_code, 302
        )

    def test_rollback_stays_an_administrator_permission(self):
        self.post(self.moderator)
        entry = ChangeLedger.objects.get()

        denied = self.client.post(
            reverse("seo:ledger_action", args=[entry.pk, "rollback"])
        )
        self.client.force_login(self.root)
        allowed = self.client.post(
            reverse("seo:ledger_action", args=[entry.pk, "rollback"])
        )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, ChangeLedger.Status.ROLLED_BACK)

    def test_group_seeding_grants_the_review_permissions(self):
        for name in ("Moderators", "Editors"):
            codenames = set(
                Group.objects.get(name=name)
                .permissions.filter(content_type__app_label="seo")
                .values_list("codename", flat=True)
            )
            self.assertEqual(
                codenames,
                {"view_issue", "change_issue", "change_metadata", "view_changeledger"},
            )
            self.assertNotIn("rollback_changeledger", codenames)
        call_command("bootstrap_site", verbosity=0)
        self.assertEqual(
            Group.objects.get(name="Moderators")
            .permissions.filter(content_type__app_label="seo")
            .count(),
            4,
        )
