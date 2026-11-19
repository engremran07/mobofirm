from __future__ import annotations

import gzip
import re
import shutil
import tempfile
from datetime import UTC, timedelta
from pathlib import Path
from typing import ClassVar
from unittest import mock
from xml.etree import ElementTree

from django.contrib.sitemaps import Sitemap
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.seo import sitemap_build, sitemap_registry
from apps.seo.models import SEOSettings
from tests.backend.seo.seo_helpers import SITE, Catalog, make_author, make_catalog

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "image": "http://www.google.com/schemas/sitemap-image/1.1",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
    "xhtml": "http://www.w3.org/1999/xhtml",
}
W3C = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def parse(response) -> ElementTree.Element:
    return ElementTree.fromstring(response.content)


@override_settings(SITE_URL=SITE)
class SitemapStructureTests(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def test_index_lists_path_based_files_with_w3c_lastmod_and_no_query_paging(self):
        index = parse(self.client.get("/sitemap.xml"))

        locations = [loc.text or "" for loc in index.findall("sm:sitemap/sm:loc", NS)]
        self.assertIn(f"{SITE}/sitemap-brands-1.xml", locations)
        self.assertIn(f"{SITE}/sitemap-models-nova-1.xml", locations)
        self.assertIn(f"{SITE}/sitemap-chipsets-mediatek-1.xml", locations)
        self.assertIn(f"{SITE}/sitemap-firmware-nova-1.xml", locations)
        self.assertFalse([loc for loc in locations if "?" in loc])
        stamps = [m.text or "" for m in index.findall("sm:sitemap/sm:lastmod", NS)]
        self.assertTrue(stamps)
        for stamp in stamps:
            self.assertRegex(stamp, W3C)

    def test_static_file_has_no_lastmod_in_index_or_section(self):
        index = parse(self.client.get("/sitemap.xml"))
        static = next(
            node
            for node in index.findall("sm:sitemap", NS)
            if (node.findtext("sm:loc", namespaces=NS) or "").endswith(
                "sitemap-static-1.xml"
            )
        )

        self.assertIsNone(static.find("sm:lastmod", NS))

    def test_section_lastmod_is_the_real_update_time_in_w3c_form(self):
        document = parse(self.client.get("/sitemap-blog-1.xml"))

        lastmod = document.findtext("sm:url/sm:lastmod", namespaces=NS) or ""
        self.assertRegex(lastmod, W3C)
        expected = self.data.post.updated_at.astimezone(UTC)
        self.assertEqual(lastmod, expected.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def test_future_or_invalid_lastmod_is_dropped_never_replaced_by_now(self):
        future = timezone.now() + timedelta(days=2)

        self.assertIsNone(sitemap_build.clean_lastmod(future))
        self.assertIsNone(sitemap_build.clean_lastmod(None))
        self.assertIsNone(sitemap_build.clean_lastmod("2020-01-01"))
        past = timezone.now() - timedelta(days=1)
        self.assertEqual(sitemap_build.clean_lastmod(past), past)

    def test_paging_is_by_path_and_respects_the_configured_page_size(self):
        with mock.patch.object(sitemap_build, "page_size", return_value=2):
            index = parse(self.client.get("/sitemap.xml"))
            locations = [
                loc.text or "" for loc in index.findall("sm:sitemap/sm:loc", NS)
            ]
            first = parse(self.client.get("/sitemap-static-1.xml"))
            last = self.client.get("/sitemap-static-4.xml")
            beyond = self.client.get("/sitemap-static-5.xml")

        self.assertIn(f"{SITE}/sitemap-static-1.xml", locations)
        self.assertIn(f"{SITE}/sitemap-static-4.xml", locations)
        self.assertNotIn(f"{SITE}/sitemap-static-5.xml", locations)
        self.assertEqual(len(first.findall("sm:url", NS)), 2)
        self.assertEqual(last.status_code, 200)
        self.assertEqual(beyond.status_code, 404)

    def test_page_size_is_capped_at_the_protocol_limit(self):
        row = SEOSettings.get_solo()
        row.sitemap_page_size = 999_999

        self.assertEqual(sitemap_build.page_size(row), 50_000)
        self.assertEqual(sitemap_build.MAX_URLS, 50_000)
        self.assertEqual(sitemap_build.MAX_BYTES, 50 * 1024 * 1024)

    def test_a_file_over_the_byte_limit_is_refused_not_truncated(self):
        with mock.patch.object(sitemap_build, "MAX_BYTES", 10):
            response = self.client.get("/sitemap-brands-1.xml")

        self.assertEqual(response.status_code, 500)

    def test_legacy_query_paging_redirects_permanently_to_the_path_form(self):
        paged = self.client.get("/sitemap-brands.xml", {"p": "2"})
        bare = self.client.get("/sitemap-brands.xml")
        partitioned = self.client.get("/sitemap-models.xml", {"p": "2"})

        self.assertEqual(paged.status_code, 301)
        self.assertEqual(paged["Location"], "/sitemap-brands-2.xml")
        self.assertEqual(bare.status_code, 301)
        self.assertEqual(bare["Location"], "/sitemap-brands-1.xml")
        self.assertEqual(partitioned.status_code, 301)
        self.assertEqual(partitioned["Location"], "/sitemap.xml")
        self.assertEqual(
            self.client.get("/sitemap-brands.xml", {"p": "abc"}).status_code, 404
        )
        self.assertEqual(self.client.get("/sitemap-nothing.xml").status_code, 404)

    def test_partitions_are_predictably_named_per_brand_and_chipset_vendor(self):
        models = parse(self.client.get("/sitemap-models-nova-1.xml"))
        chipsets = parse(self.client.get("/sitemap-chipsets-mediatek-1.xml"))

        self.assertEqual(len(models.findall("sm:url", NS)), 1)
        self.assertIn(
            "/firmwares/nova/atlas-one/",
            models.findtext("sm:url/sm:loc", namespaces=NS) or "",
        )
        self.assertEqual(len(chipsets.findall("sm:url", NS)), 1)
        self.assertEqual(
            self.client.get("/sitemap-models-other-1.xml").status_code, 404
        )

    def test_hreflang_alternates_only_when_a_section_declares_them(self):
        class Localised(Sitemap):
            def items(self):
                return ["/a/", "/b/"]

            def location(self, item):
                return item

            def alternates(self, item):
                if item == "/a/":
                    return {"en": f"{SITE}/a/", "fr": f"{SITE}/fr/a/"}
                return {"en": f"{SITE}/b/"}

        sitemap_registry.register_sitemap("local", Localised)
        self.addCleanup(sitemap_registry.unregister_sitemap, "local")

        document = parse(self.client.get("/sitemap-local-1.xml"))

        links = document.findall("sm:url/xhtml:link", NS)
        self.assertEqual({link.get("hreflang") for link in links}, {"en", "fr"})
        self.assertEqual(len(links), 2)
        plain = parse(self.client.get("/sitemap-brands-1.xml"))
        self.assertEqual(plain.findall("sm:url/xhtml:link", NS), [])


@override_settings(SITE_URL=SITE)
class SitemapHttpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        make_catalog()

    def test_validators_cache_control_and_noindex_header(self):
        response = self.client.get("/sitemap-brands-1.xml")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["ETag"].startswith('"'))
        self.assertIn("Last-Modified", response)
        self.assertIn("max-age=300", response["Cache-Control"])
        self.assertIn("stale-while-revalidate", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")

    def test_conditional_get_returns_304_for_etag_and_modified_since(self):
        first = self.client.get("/sitemap-brands-1.xml")

        by_etag = self.client.get(
            "/sitemap-brands-1.xml", headers={"If-None-Match": first["ETag"]}
        )
        by_date = self.client.get(
            "/sitemap-brands-1.xml",
            headers={"If-Modified-Since": first["Last-Modified"]},
        )
        stale = self.client.get(
            "/sitemap-brands-1.xml", headers={"If-None-Match": '"other"'}
        )

        self.assertEqual(by_etag.status_code, 304)
        self.assertEqual(by_etag.content, b"")
        self.assertEqual(by_etag["ETag"], first["ETag"])
        self.assertEqual(by_date.status_code, 304)
        self.assertEqual(stale.status_code, 200)

    def test_head_is_supported_without_a_body_and_other_methods_are_refused(self):
        get = self.client.get("/sitemap.xml")
        head = self.client.head("/sitemap.xml")

        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")
        self.assertEqual(head["Content-Length"], str(len(get.content)))
        self.assertEqual(head["ETag"], get["ETag"])
        self.assertEqual(self.client.post("/sitemap.xml").status_code, 405)

    def test_gzip_variants_decompress_to_the_plain_document(self):
        for plain, packed in (
            ("/sitemap.xml", "/sitemap.xml.gz"),
            ("/sitemap-brands-1.xml", "/sitemap-brands-1.xml.gz"),
        ):
            with self.subTest(path=packed):
                response = self.client.get(packed)
                self.assertEqual(response["Content-Type"], "application/gzip")
                self.assertNotIn("Content-Encoding", response)
                self.assertEqual(
                    gzip.decompress(response.content), self.client.get(plain).content
                )
                self.assertNotEqual(response["ETag"], self.client.get(plain)["ETag"])
        self.assertEqual(self.client.get("/sitemap-brands-9.xml.gz").status_code, 404)

    def test_the_stylesheet_is_self_hosted_css_not_xslt_and_strict_csp_safe(self):
        css_path = finders.find("seo/sitemap.css")
        body = self.client.get("/sitemap.xml").content.decode()

        self.assertIsInstance(css_path, str)
        with open(str(css_path), encoding="utf-8") as handle:
            self.assertIn("display: table", handle.read())
        self.assertNotIn("xsl", body.lower())
        self.assertEqual(self.client.get("/sitemap.xsl").status_code, 404)
        self.assertNotIn("style=", body)


@override_settings(SITE_URL=SITE)
class SitemapImageTests(TestCase):
    data: ClassVar[Catalog]

    def setUp(self):
        self.data = make_catalog()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def _store(self, name: str) -> None:
        target = Path(self.media) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n")

    def _brand_images(self) -> list[str]:
        document = parse(self.client.get("/sitemap-brands-1.xml"))
        return [
            node.text or ""
            for node in document.findall("sm:url/image:image/image:loc", NS)
        ]

    def test_an_existing_site_image_is_listed_with_the_image_namespace(self):
        self._store("brands/nova.png")
        self.data.brand.logo_url = "/media/brands/nova.png"
        self.data.brand.save()

        self.assertEqual(self._brand_images(), [f"{SITE}/media/brands/nova.png"])
        body = self.client.get("/sitemap-brands-1.xml").content.decode()
        self.assertIn(
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"', body
        )

    def test_unverified_images_are_never_listed(self):
        for value in (
            "/media/brands/missing.png",
            "https://cdn.elsewhere.example/logo.png",
            "/media/brands/notes.txt",
            "/storage/secret.png",
            "",
        ):
            with self.subTest(value=value):
                self._store("brands/notes.txt")
                self.data.brand.logo_url = value
                self.data.brand.save()
                self.assertEqual(self._brand_images(), [])

    def test_images_can_be_switched_off(self):
        self._store("brands/nova.png")
        self.data.brand.logo_url = "/media/brands/nova.png"
        self.data.brand.save()
        row = SEOSettings.get_solo()
        row.sitemap_images_enabled = False
        row.save()

        self.assertEqual(self._brand_images(), [])

    def test_an_image_blocked_for_googlebot_image_is_not_advertised(self):
        self._store("brands/nova.png")
        self.data.brand.logo_url = "/media/brands/nova.png"
        self.data.brand.save()

        with mock.patch(
            "apps.seo.sitemap_build.robots.generate",
            return_value="User-agent: Googlebot-Image\nDisallow: /media/\n",
        ):
            self.assertEqual(self._brand_images(), [])


@override_settings(SITE_URL=SITE)
class NewsSitemapTests(TestCase):
    data: ClassVar[Catalog]

    def setUp(self):
        self.data = make_catalog()

    def _enable(self):
        row = SEOSettings.get_solo()
        row.sitemap_news_enabled = True
        row.save()

    def test_news_is_off_by_default_and_absent_from_index_and_robots(self):
        index = self.client.get("/sitemap.xml").content.decode()

        self.assertEqual(self.client.get("/sitemap-news.xml").status_code, 404)
        self.assertNotIn("sitemap-news.xml", index)
        self.assertNotIn(
            "sitemap-news.xml", self.client.get("/robots.txt").content.decode()
        )

    def test_enabled_news_lists_only_fresh_indexable_posts(self):
        author = make_author("newsroom")
        old = Post.objects.create(
            title="Old story",
            slug="old-story",
            body="<p>x</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(days=5),
        )
        hidden = Post.objects.create(
            title="Hidden story",
            slug="hidden-story",
            body="<p>x</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
            noindex=True,
        )
        self._enable()

        response = self.client.get("/sitemap-news.xml")
        document = parse(response)

        self.assertEqual(response.status_code, 200)
        locs = [node.text or "" for node in document.findall("sm:url/sm:loc", NS)]
        self.assertEqual(locs, [f"{SITE}{self.data.post.get_absolute_url()}"])
        self.assertNotIn(old.slug, " ".join(locs))
        self.assertNotIn(hidden.slug, " ".join(locs))
        published = document.findtext(
            "sm:url/news:news/news:publication_date", namespaces=NS
        )
        self.assertRegex(published or "", W3C)
        self.assertEqual(
            document.findtext("sm:url/news:news/news:title", namespaces=NS),
            self.data.post.title,
        )
        self.assertIn(
            "sitemap-news.xml", self.client.get("/sitemap.xml").content.decode()
        )
        self.assertIn(
            f"Sitemap: {SITE}/sitemap-news.xml",
            self.client.get("/robots.txt").content.decode(),
        )
