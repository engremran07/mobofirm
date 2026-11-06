from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit
from xml.etree import ElementTree

from django.contrib.sitemaps import Sitemap
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.pages.models import Page
from apps.seo import sitemap_registry
from apps.seo.inventory import resolve_path
from apps.seo.models import SEOSettings
from apps.seo.policy import is_private_path
from apps.tags.models import Tag
from tests.backend.seo.seo_helpers import (
    SITE,
    Catalog,
    make_author,
    make_catalog,
)

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def sitemap_locations(client) -> dict[str, list[ElementTree.Element]]:
    index = ElementTree.fromstring(client.get("/sitemap.xml").content)
    result: dict[str, list[ElementTree.Element]] = {}
    for loc in index.findall("sm:sitemap/sm:loc", NS):
        section_url = urlsplit(loc.text or "")
        path = section_url.path + (f"?{section_url.query}" if section_url.query else "")
        document = ElementTree.fromstring(client.get(path).content)
        result[section_url.path] = document.findall("sm:url", NS)
    return result


@override_settings(SITE_URL=SITE)
class SitemapReconciliationTests(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def test_registry_wires_every_installed_content_app(self):
        self.assertEqual(
            set(sitemap_registry.get_sitemaps()),
            {
                "static",
                "pages",
                "blog",
                "brands",
                "chipsets",
                "models",
                "firmware",
                "tools",
                "tags",
            },
        )

    def test_every_listed_url_is_absolute_public_indexable_and_self_canonical(self):
        sections = sitemap_locations(self.client)
        self.assertTrue(sections)
        checked = 0
        for section, urls in sections.items():
            for url in urls:
                location = url.findtext("sm:loc", namespaces=NS) or ""
                with self.subTest(section=section, url=location):
                    self.assertTrue(location.startswith(f"{SITE}/"))
                    path = urlsplit(location).path
                    self.assertFalse(is_private_path(path))
                    resolved = resolve_path(path)
                    assert resolved is not None
                    self.assertTrue(resolved.indexable, resolved.noindex_reason)
                    self.assertEqual(resolved.canonical, location)
                    checked += 1
        self.assertGreater(checked, 8)

    def test_all_catalogue_page_types_appear(self):
        listed = {
            urlsplit(url.findtext("sm:loc", namespaces=NS) or "").path
            for urls in sitemap_locations(self.client).values()
            for url in urls
        }

        for path in (
            reverse("firmwares:brand_detail", args=["nova"]),
            reverse("firmwares:model_detail", args=["nova", "atlas-one"]),
            reverse("firmwares:chipset_detail", args=["mediatek-helio-g100"]),
            reverse(
                "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
            ),
            reverse("firmwares:flashing_tool_detail", args=["flasher"]),
            self.data.post.get_absolute_url(),
            reverse("tags:detail", args=["flashing"]),
            reverse("pages:page", args=["about"]),
        ):
            self.assertIn(path, listed)

    def test_priority_and_changefreq_are_not_emitted_and_lastmod_is_real(self):
        response = self.client.get("/sitemap-brands.xml")
        document = ElementTree.fromstring(response.content)

        self.assertNotIn(b"<priority>", response.content)
        self.assertNotIn(b"<changefreq>", response.content)
        lastmod = document.findtext("sm:url/sm:lastmod", namespaces=NS)
        self.assertTrue(
            (lastmod or "").startswith(str(self.data.brand.updated_at.year))
        )

    def test_static_entries_carry_no_invented_lastmod(self):
        document = ElementTree.fromstring(
            self.client.get("/sitemap-static.xml").content
        )

        self.assertTrue(document.findall("sm:url", NS))
        self.assertEqual(document.findall("sm:url/sm:lastmod", NS), [])

    def test_xml_uses_a_self_hosted_stylesheet_not_inline_style(self):
        for path in ("/sitemap.xml", "/sitemap-brands.xml"):
            response = self.client.get(path)
            body = response.content.decode()
            self.assertTrue(body.startswith("<?xml"))
            self.assertIn(
                '<?xml-stylesheet type="text/css" href="/static/build/seo-sitemap.css"?>',
                body,
            )
            self.assertNotIn("<style", body)
            self.assertEqual(response["Content-Type"], "application/xml")

    def test_thin_and_non_canonical_items_are_excluded(self):
        author = make_author("other")
        empty_brand = type(self.data.brand).objects.create(name="Empty", slug="empty")
        Tag.objects.create(name="Lonely", slug="lonely", is_curated=True)
        Post.objects.create(
            title="Syndicated copy",
            slug="syndicated-copy",
            body="<p>x</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
            canonical_url="https://elsewhere.example/original/",
        )
        Post.objects.create(
            title="Hidden post",
            slug="hidden-post",
            body="<p>x</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
            noindex=True,
        )
        Page.objects.create(
            slug="moved",
            title="Moved",
            content="x",
            status="published",
            canonical_url="https://elsewhere.example/moved/",
        )
        Page.objects.create(slug="draft-page", title="Draft", content="x")
        self.data.firmware.is_verified = False
        self.data.firmware.save()

        listed = {
            urlsplit(url.findtext("sm:loc", namespaces=NS) or "").path
            for urls in sitemap_locations(self.client).values()
            for url in urls
        }

        self.assertNotIn(f"/firmwares/{empty_brand.slug}/", listed)
        self.assertNotIn("/tags/lonely/", listed)
        self.assertNotIn("/blog/syndicated-copy/", listed)
        self.assertNotIn("/blog/hidden-post/", listed)
        self.assertNotIn("/pages/moved/", listed)
        self.assertNotIn("/pages/draft-page/", listed)
        self.assertNotIn("/firmwares/nova/atlas-one/", listed)
        self.assertNotIn(
            reverse(
                "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
            ),
            listed,
        )

    def test_sitemap_filter_registered_by_an_app_is_applied(self):
        class Extra(Sitemap):
            def items(self):
                return ["/a/", "/b/"]

            def location(self, item):
                return item

        sitemap_registry.register_sitemap("extra", Extra)
        sitemap_registry.register_sitemap_filter(
            "extra", lambda items, row: [item for item in items if item == "/a/"]
        )
        self.addCleanup(sitemap_registry.unregister_sitemap, "extra")
        self.addCleanup(sitemap_registry._filters.pop, "extra", None)

        self.assertEqual(sitemap_registry.get_sitemaps()["extra"]().items(), ["/a/"])

    def test_sitemap_is_disabled_by_seo_settings(self):
        row = SEOSettings.get_solo()
        row.sitemap_enabled = False
        row.save()

        self.assertEqual(self.client.get("/sitemap.xml").status_code, 404)
        self.assertEqual(self.client.get("/sitemap-brands.xml").status_code, 404)
        self.assertNotContains(self.client.get("/robots.txt"), "Sitemap:")


@override_settings(SITE_URL=SITE)
class RobotsTests(TestCase):
    def test_default_policy_blocks_private_areas_and_training_bots_only(self):
        body = self.client.get("/robots.txt").content.decode()

        self.assertIn("User-agent: *\nDisallow: /admin/", body)
        self.assertIn("Disallow: /accounts/", body)
        self.assertIn(f"Sitemap: {SITE}/sitemap.xml", body)
        self.assertIn("User-agent: GPTBot", body)
        self.assertIn("User-agent: Google-Extended", body)
        self.assertNotIn("User-agent: OAI-SearchBot", body)
        self.assertNotIn("User-agent: ChatGPT-User", body)

    def test_ai_crawler_groups_follow_the_single_settings_row(self):
        row = SEOSettings.get_solo()
        row.allow_ai_search_bots = False
        row.allow_ai_user_agents = False
        row.allow_ai_training_bots = True
        row.save()

        body = self.client.get("/robots.txt").content.decode()

        self.assertIn("User-agent: OAI-SearchBot", body)
        self.assertIn("User-agent: ChatGPT-User", body)
        self.assertNotIn("User-agent: GPTBot", body)
        blocked_group = body.split("User-agent: OAI-SearchBot")[1].split("\n\n")[0]
        self.assertIn("Disallow: /", blocked_group)

    def test_thin_pages_use_noindex_not_disallow(self):
        body = self.client.get("/robots.txt").content.decode()

        self.assertNotIn("Disallow: /firmwares/browse", body)
        self.assertNotIn("Disallow: /blog/search", body)

    def test_robots_is_plain_text(self):
        self.assertEqual(self.client.get("/robots.txt")["Content-Type"], "text/plain")


@override_settings(SITE_URL=SITE)
class IndexNowKeyTests(TestCase):
    def test_key_file_is_served_only_when_enabled_and_matching(self):
        row = SEOSettings.get_solo()
        row.indexnow_enabled = True
        row.indexnow_key = "abc123def4567890"
        row.save()

        ok = self.client.get("/abc123def4567890.txt")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content.decode(), "abc123def4567890")
        self.assertEqual(self.client.get("/wrongkey1234567.txt").status_code, 404)

        row.indexnow_enabled = False
        row.save()
        self.assertEqual(self.client.get("/abc123def4567890.txt").status_code, 404)

    def test_robots_route_still_wins_over_the_key_pattern(self):
        self.assertEqual(self.client.get("/robots.txt").status_code, 200)


class HeaderPolicyTests(TestCase):
    def test_private_and_json_responses_carry_x_robots_tag(self):
        self.assertEqual(
            self.client.get("/admin/login/")["X-Robots-Tag"], "noindex, nofollow"
        )
        self.assertEqual(
            self.client.get("/health/live/")["X-Robots-Tag"], "noindex, nofollow"
        )

    def test_public_html_pages_do_not_get_the_header(self):
        self.assertNotIn("X-Robots-Tag", self.client.get("/firmwares/brands/"))
