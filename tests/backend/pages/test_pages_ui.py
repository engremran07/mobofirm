from __future__ import annotations

import re
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve, reverse

from apps.firmwares.models import (
    Brand,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
)
from apps.pages import seed, widgets
from apps.pages.models import Page
from apps.pages.rendering import render_page

EXPECTED_SLUGS = {
    "about",
    "contact",
    "privacy",
    "terms",
    "consent",
    "dmca",
    "firmware-disclaimer",
    "how-to-flash",
    "faq",
    "advertise",
}
FORBIDDEN_CLAIMS = ("bounty", "tester programme", "gsmfws")


def run_seed(*args: str) -> tuple[str, str]:
    out, err = StringIO(), StringIO()
    call_command("seed_pages", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class SeedPagesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_seed_publishes_every_expected_page_with_a_sitemap_entry(self):
        run_seed()

        pages = {page.slug: page for page in Page.objects.all()}
        self.assertEqual(set(pages), EXPECTED_SLUGS)
        for page in pages.values():
            with self.subTest(slug=page.slug):
                self.assertTrue(page.is_published)
                self.assertTrue(page.include_in_sitemap)
                self.assertTrue(page.seo_description)
                self.assertGreater(len(page.content.split()), 80)

    def test_seed_is_idempotent_and_keeps_staff_edits(self):
        run_seed()
        Page.objects.filter(slug="about").update(content="Edited by staff.")

        out, _ = run_seed()

        self.assertIn("created: 0", out)
        self.assertEqual(Page.objects.count(), len(EXPECTED_SLUGS))
        self.assertEqual(Page.objects.get(slug="about").content, "Edited by staff.")

    def test_force_restores_only_pages_that_drifted(self):
        run_seed()
        Page.objects.filter(slug="about").update(content="Edited by staff.")

        out, _ = run_seed("--force")

        self.assertIn("updated: 1", out)
        self.assertNotEqual(Page.objects.get(slug="about").content, "Edited by staff.")

    def test_seed_is_deterministic(self):
        run_seed()
        first = {page.slug: page.content for page in Page.objects.all()}
        Page.objects.all().delete()
        run_seed()

        self.assertEqual(
            {page.slug: page.content for page in Page.objects.all()}, first
        )

    @override_settings(SITE_NAME="Acme Firmware", SITE_CONTACT_EMAIL="help@acme.test")
    def test_site_name_and_contact_address_are_substituted(self):
        _, err = run_seed()

        contact = Page.objects.get(slug="contact").content
        self.assertIn("help@acme.test", contact)
        self.assertIn("Acme Firmware", Page.objects.get(slug="about").content)
        self.assertNotIn("[[", " ".join(page.content for page in Page.objects.all()))
        self.assertEqual(err, "")

    def test_dummy_contact_address_is_reported(self):
        _, err = run_seed()

        self.assertIn("dummy value", err)

    def test_pages_do_not_claim_programmes_that_do_not_exist(self):
        run_seed()

        text = " ".join(page.content for page in Page.objects.all()).lower()
        for claim in FORBIDDEN_CLAIMS:
            self.assertNotIn(claim, text)

    def test_internal_links_resolve_to_real_routes(self):
        run_seed()

        links = set()
        for page in Page.objects.all():
            links.update(re.findall(r'href="(/[^"#]*)"', page.rendered.html))
        self.assertTrue(links)
        for link in sorted(links):
            with self.subTest(link=link):
                try:
                    resolve(link)
                except Resolver404:
                    self.fail(f"Seeded page links to unknown route {link}")

    def test_seeded_legal_pages_serve_and_footer_links_resolve(self):
        run_seed()

        for slug in ("privacy", "terms", "consent"):
            response = self.client.get(reverse("pages:page", kwargs={"slug": slug}))
            self.assertEqual(response.status_code, 200, slug)
        footer = self.client.get(reverse("home")).content.decode()
        for slug in ("about", "contact", "faq", "how-to-flash", "dmca", "advertise"):
            self.assertIn(f'href="/pages/{slug}/"', footer)

    def test_every_seed_definition_has_a_unique_slug(self):
        slugs = [page.slug for page in seed.SEED_PAGES]

        self.assertEqual(len(slugs), len(set(slugs)))


class PageTemplateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.page = Page.objects.create(
            slug="guide",
            title="Flashing guide",
            status="published",
            content="Intro\n\n## One\n\ntext\n\n## Two\n\ntext\n\n### Two A\n\ntext",
        )

    def test_toc_is_built_from_markdown_headings(self):
        rendered = render_page(self.page.content, "md")

        self.assertEqual(
            [(entry.level, entry.title) for entry in rendered.toc],
            [(2, "One"), (2, "Two"), (3, "Two A")],
        )
        self.assertIn('id="section-one"', rendered.html)

    def test_page_shows_breadcrumbs_contents_and_last_updated(self):
        response = self.client.get(self.page.get_absolute_url())

        self.assertContains(response, 'aria-label="Breadcrumb"')
        self.assertContains(response, 'href="#section-one"')
        self.assertContains(response, "On this page")
        self.assertContains(response, "Last updated")
        self.assertContains(response, self.page.updated_at.strftime("%Y"))
        self.assertContains(response, "min read")

    def test_short_pages_have_no_contents_box(self):
        Page.objects.filter(pk=self.page.pk).update(content="Just one paragraph.")

        response = self.client.get(self.page.get_absolute_url())

        self.assertNotContains(response, "On this page")

    def test_html_pages_render_without_contents(self):
        Page.objects.filter(pk=self.page.pk).update(
            content="<h2>Heading</h2><p>Body</p>", content_format="html"
        )

        response = self.client.get(self.page.get_absolute_url())

        self.assertContains(response, "<h2>Heading</h2>", html=True)
        self.assertNotContains(response, "On this page")

    def test_footer_lists_only_published_information_pages(self):
        html = self.client.get(reverse("home")).content.decode()
        self.assertNotIn('href="/pages/about/"', html)

        Page.objects.create(
            slug="about", title="About", status="published", content="x"
        )
        cache.clear()
        html = self.client.get(reverse("home")).content.decode()

        self.assertIn('href="/pages/about/"', html)
        self.assertNotIn('href="/pages/contact/"', html)
        for slug in ("privacy", "terms", "consent"):
            self.assertNotIn(f'href="/pages/{slug}/"', html)


class PublishedSlugCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_firmware_changes_keep_the_slug_cache_but_page_changes_drop_it(self):
        Page.objects.create(
            slug="about", title="About", status="published", content="x"
        )
        self.assertEqual(widgets.published_page_slugs(), {"about"})

        brand = Brand.objects.create(name="Slug Brand", slug="slug-brand")
        model = Model.objects.create(brand=brand, name="S1", slug="s1")
        OfficialFirmware.objects.create(
            original_file_name="slug.zip",
            stored_file_path="/firmware/slug.zip",
            brand=brand,
            model=model,
            is_verified=True,
            is_active=True,
        )
        with self.assertNumQueries(0):
            self.assertEqual(widgets.published_page_slugs(), {"about"})

        Page.objects.create(slug="faq", title="FAQ", status="published", content="x")
        self.assertEqual(widgets.published_page_slugs(), {"about", "faq"})


class HomepageWidgetTests(TestCase):
    def setUp(self):
        cache.clear()
        brand = Brand.objects.create(name="Widget Brand", slug="widget-brand")
        model = Model.objects.create(brand=brand, name="W1", slug="w1")
        OfficialFirmware.objects.create(
            original_file_name="widget.zip",
            stored_file_path="/firmware/widget.zip",
            brand=brand,
            model=model,
            is_verified=True,
            is_active=True,
        )
        OfficialFirmware.objects.create(
            original_file_name="pending.zip",
            stored_file_path="/firmware/pending.zip",
            brand=brand,
            model=model,
            is_verified=False,
        )
        category = FlashingToolCategory.objects.create(name="Boxes", slug="boxes")
        FlashingTool.objects.create(
            category=category, name="Flash Helper", slug="flash-helper", is_active=True
        )
        FlashingTool.objects.create(
            category=category, name="Retired Tool", slug="retired", is_active=False
        )

    def test_homepage_shows_latest_firmware_and_tools_from_pages_widgets(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, "Fresh from the library")
        self.assertContains(response, "Widget Brand W1")
        self.assertContains(response, "Flash Helper")
        self.assertNotContains(response, "Retired Tool")

    def test_unreviewed_firmware_never_reaches_the_homepage(self):
        names = [item["file_name"] for item in widgets.latest_firmwares()]

        self.assertEqual(names, ["widget.zip"])

    def test_widgets_disappear_when_the_pages_app_is_switched_off(self):
        from apps.core.models import AppRegistry

        registry = AppRegistry.get_solo()
        registry.pages_enabled = False
        registry.save()

        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "Fresh from the library")
        self.assertNotContains(response, 'href="/pages/"')

    def test_pages_index_lists_the_same_widgets(self):
        response = self.client.get(reverse("pages:index"))

        self.assertContains(response, "Flash Helper")
        self.assertContains(response, "Latest firmware")
