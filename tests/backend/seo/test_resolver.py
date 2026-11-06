from __future__ import annotations

import json
from typing import ClassVar

import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.blog.models import Post
from apps.seo.crawler import parse_html
from apps.seo.inventory import iter_static_routes, resolve_path
from apps.seo.models import Metadata, SEOModel, SEOSettings
from apps.seo.providers import registered_view_names
from apps.seo.schema import to_json_ld
from tests.backend.seo.seo_helpers import SITE, Catalog, make_catalog


def head_of(response):
    return parse_html(response.content.decode())


@override_settings(SITE_URL=SITE)
class SingleHeadContractTests(TestCase):
    """Exactly one title, description, canonical and robots tag per page."""

    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def assert_single_head(self, path, *, canonical, indexable=True):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        head = head_of(response)
        self.assertEqual(len(head.titles), 1, f"{path}: titles {head.titles}")
        self.assertEqual(len(head.descriptions), 1, f"{path}: descriptions")
        self.assertEqual(head.canonicals, [canonical], path)
        self.assertEqual(len(head.robots_meta), 1, path)
        self.assertEqual("noindex" not in head.robots_meta[0], indexable, path)
        self.assertTrue(head.canonicals[0].startswith("https://example.org/"))
        self.assertTrue(head.titles[0].strip())
        return head

    def test_home_has_site_nodes_and_absolute_canonical(self):
        head = self.assert_single_head("/", canonical=f"{SITE}/")

        self.assertIn("Organization", head.jsonld_types)
        self.assertIn("WebSite", head.jsonld_types)
        self.assertEqual(head.jsonld_errors, 0)
        html = self.client.get("/").content.decode()
        self.assertIn("SearchAction", html)
        self.assertIn("search_term_string", html)

    def test_library_hub_duplicate_canonicalises_to_home(self):
        self.assert_single_head(reverse("firmwares:library_hub"), canonical=f"{SITE}/")

    def test_catalogue_pages(self):
        brand_path = reverse("firmwares:brand_detail", args=["nova"])
        model_path = reverse("firmwares:model_detail", args=["nova", "atlas-one"])
        chipset_path = reverse("firmwares:chipset_detail", args=["mediatek-helio-g100"])
        firmware_path = reverse(
            "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
        )
        for path in (
            reverse("firmwares:brand_list"),
            brand_path,
            model_path,
            chipset_path,
            reverse("firmwares:chipset_list"),
            firmware_path,
            reverse("firmwares:flashing_tools"),
            reverse("firmwares:flashing_tool_detail", args=["flasher"]),
            reverse("firmwares:flashing_guides"),
            reverse("firmwares:browse"),
        ):
            with self.subTest(path=path):
                self.assert_single_head(path, canonical=f"{SITE}{path}")

    def test_model_page_describes_only_real_facts(self):
        path = reverse("firmwares:model_detail", args=["nova", "atlas-one"])
        head = head_of(self.client.get(path))

        self.assertIn("Atlas One", head.titles[0])
        self.assertEqual(head.descriptions[0], "The Atlas One is a mid-range phone.")
        self.assertIn("CollectionPage", head.jsonld_types)
        self.assertIn("BreadcrumbList", head.jsonld_types)

    def test_firmware_page_uses_only_stored_facts_and_no_software_schema(self):
        path = reverse(
            "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
        )
        response = self.client.get(path)
        head = head_of(response)

        self.assertIn("V1.0.3", head.titles[0])
        self.assertIn("Android 14", head.descriptions[0])
        self.assertNotIn("SoftwareApplication", head.jsonld_types)
        self.assertNotIn("sha", head.descriptions[0].lower())

    def test_firmware_type_aliases_share_one_canonical(self):
        alias = reverse(
            "firmwares:firmware_detail",
            args=["officialfirmware", self.data.firmware.pk],
        )
        canonical = reverse(
            "firmwares:firmware_detail", args=["official", self.data.firmware.pk]
        )

        head = head_of(self.client.get(alias))

        self.assertEqual(head.canonicals, [f"{SITE}{canonical}"])

    def test_flashing_tool_uses_software_application_schema(self):
        path = reverse("firmwares:flashing_tool_detail", args=["flasher"])
        head = head_of(self.client.get(path))

        self.assertIn("SoftwareApplication", head.jsonld_types)

    def test_blog_pages(self):
        post_path = self.data.post.get_absolute_url()
        publish_at = self.data.post.publish_at
        assert publish_at is not None
        for path in (
            reverse("blog:post_list"),
            post_path,
            f"{reverse('blog:post_list')}?category=guides",
            reverse("blog:post_archive_year", args=[publish_at.year]),
            reverse("blog:brand_posts", args=["nova"]),
            reverse("blog:model_posts", args=["nova", "atlas-one"]),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                head = head_of(response)
                self.assertEqual(len(head.titles), 1, path)
                self.assertEqual(len(head.canonicals), 1, path)
                self.assertTrue(head.canonicals[0].startswith(SITE), path)

    def test_blog_post_article_schema_never_invents_dates(self):
        response = self.client.get(self.data.post.get_absolute_url())
        html = response.content.decode()
        blocks = [
            json.loads(block.split("</script>")[0])
            for block in html.split('<script type="application/ld+json">')[1:]
        ]
        article = next(block for block in blocks if block["@type"] == "BlogPosting")

        post = Post.objects.get(pk=self.data.post.pk)
        assert post.published_at is not None
        self.assertEqual(article["datePublished"], post.published_at.isoformat())
        self.assertEqual(article["dateModified"], post.updated_at.isoformat())
        self.assertEqual(article["url"], f"{SITE}{post.get_absolute_url()}")

    def test_tag_and_static_pages(self):
        for path in (
            reverse("tags:list"),
            reverse("tags:detail", args=["flashing"]),
            reverse("pages:index"),
            reverse("pages:page", args=["about"]),
        ):
            with self.subTest(path=path):
                self.assert_single_head(path, canonical=f"{SITE}{path}")

    def test_search_pages_are_noindex(self):
        for path in (
            f"{reverse('firmwares:universal_search')}?q=atlas",
            f"{reverse('blog:blog_search')}?q=atlas",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                head = head_of(response)
                self.assertEqual(len(head.robots_meta), 1)
                self.assertTrue(head.robots_meta[0].startswith("noindex"))
                self.assertEqual(head.jsonld_types, [])

    def test_parameterised_variants_are_noindex_and_canonicalise_to_the_base(self):
        browse = reverse("firmwares:browse")
        head = head_of(self.client.get(f"{browse}?q=atlas&utm_source=ad"))

        self.assertTrue(head.robots_meta[0].startswith("noindex"))
        self.assertEqual(head.canonicals, [f"{SITE}{browse}"])

    def test_pagination_keeps_page_two_indexable_with_its_own_canonical(self):
        resolved = resolve_path(f"{reverse('blog:post_list')}?page=2")

        assert resolved is not None
        self.assertTrue(resolved.indexable)
        self.assertEqual(
            resolved.canonical, f"{SITE}{reverse('blog:post_list')}?page=2"
        )
        first = resolve_path(f"{reverse('blog:post_list')}?page=1")
        assert first is not None
        self.assertEqual(first.canonical, f"{SITE}{reverse('blog:post_list')}")

    def test_tracking_parameters_never_change_the_canonical(self):
        resolved = resolve_path("/firmwares/brands/?utm_source=x&gclid=1")

        assert resolved is not None
        self.assertTrue(resolved.indexable)
        self.assertEqual(resolved.canonical, f"{SITE}/firmwares/brands/")

    def test_private_download_and_unmapped_routes_are_noindex_with_reasons(self):
        file_id = self.data.firmware.pk
        cases = {
            f"/firmwares/file/official/{file_id}/download/": "private",
            "/users/profile/": "private",
            "/accounts/login/": "private",
            "/admin/": "private",
            "/health/live/": "private",
        }
        for path, page_type in cases.items():
            with self.subTest(path=path):
                resolved = resolve_path(path)
                assert resolved is not None
                self.assertFalse(resolved.indexable)
                self.assertTrue(resolved.robots.startswith("noindex"))
                self.assertEqual(resolved.page_type, page_type)
                self.assertTrue(resolved.noindex_reason)

    def test_routes_without_a_provider_default_to_noindex(self):
        unmapped = [
            path
            for name, path in iter_static_routes()
            if name not in registered_view_names() and name != "home"
        ]
        self.assertTrue(unmapped)
        for path in unmapped:
            with self.subTest(path=path):
                resolved = resolve_path(path)
                if resolved is not None:
                    self.assertFalse(resolved.indexable)

    def test_missing_pages_render_a_single_noindex_head(self):
        not_found = self.client.get("/definitely-missing/")
        head = head_of(not_found)
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(len(head.titles), 1)
        self.assertTrue(head.robots_meta[0].startswith("noindex"))


@override_settings(SITE_URL=SITE)
class ThinPagePolicyTests(TestCase):
    data: ClassVar[Catalog]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()

    def test_model_without_public_firmware_is_noindex(self):
        self.data.firmware.is_verified = False
        self.data.firmware.save()

        resolved = resolve_path(
            reverse("firmwares:model_detail", args=["nova", "atlas-one"])
        )

        assert resolved is not None
        self.assertFalse(resolved.indexable)
        self.assertIn("fewer than 1", resolved.noindex_reason)
        self.assertEqual(resolved.schema, ())

    def test_threshold_is_configurable_in_the_single_settings_row(self):
        row = SEOSettings.get_solo()
        row.min_firmware_for_model_index = 2
        row.save()

        resolved = resolve_path(
            reverse("firmwares:model_detail", args=["nova", "atlas-one"])
        )

        assert resolved is not None
        self.assertFalse(resolved.indexable)

    def test_brand_chipset_and_tag_pages_follow_the_same_policy(self):
        self.data.model.is_active = False
        self.data.model.save()
        self.data.post.noindex = True
        self.data.post.save()

        for path in (
            reverse("firmwares:brand_detail", args=["nova"]),
            reverse("firmwares:chipset_detail", args=["mediatek-helio-g100"]),
            reverse("tags:detail", args=["flashing"]),
        ):
            with self.subTest(path=path):
                resolved = resolve_path(path)
                assert resolved is not None
                self.assertFalse(resolved.indexable)

    def test_noindex_blog_post_stays_out(self):
        self.data.post.noindex = True
        self.data.post.save()

        resolved = resolve_path(self.data.post.get_absolute_url())

        assert resolved is not None
        self.assertFalse(resolved.indexable)


@override_settings(SITE_URL=SITE)
class OverridePrecedenceTests(TestCase):
    data: ClassVar[Catalog]
    path: ClassVar[str]

    @classmethod
    def setUpTestData(cls):
        cls.data = make_catalog()
        cls.path = reverse("firmwares:model_detail", args=["nova", "atlas-one"])

    def override(self, **fields):
        seo = SEOModel.objects.create(
            content_type=ContentType.objects.get_for_model(type(self.data.model)),
            object_id=str(self.data.model.pk),
        )
        return Metadata.objects.create(seo=seo, **fields)

    def test_admin_override_wins_and_records_its_provenance(self):
        self.override(
            meta_title="Custom Atlas title",
            meta_description="Custom description.",
            canonical_url="https://example.org/custom/",
        )

        resolved = resolve_path(self.path)

        assert resolved is not None
        self.assertEqual(resolved.title, "Custom Atlas title")
        self.assertEqual(resolved.description, "Custom description.")
        self.assertEqual(resolved.canonical, "https://example.org/custom/")
        self.assertEqual(resolved.provenance["title"], "override")
        self.assertEqual(resolved.provenance["canonical"], "override")

    def test_object_data_and_defaults_report_their_origin(self):
        resolved = resolve_path(self.path)

        assert resolved is not None
        self.assertEqual(resolved.provenance["description"], "object")
        self.assertEqual(resolved.provenance["robots"], "default")

    def test_override_cannot_force_index_on_a_policy_noindex_page(self):
        self.override(robots_directives="index, follow", noindex=False)
        self.data.firmware.is_verified = False
        self.data.firmware.save()

        resolved = resolve_path(self.path)

        assert resolved is not None
        self.assertFalse(resolved.indexable)
        self.assertEqual(resolved.provenance["robots"], "policy")

    def test_override_noindex_is_honoured(self):
        self.override(noindex=True)

        resolved = resolve_path(self.path)

        assert resolved is not None
        self.assertFalse(resolved.indexable)
        self.assertEqual(resolved.noindex_reason, "administrator override")

    def test_invalid_robots_override_is_ignored(self):
        self.override(robots_directives="index, <script>")

        resolved = resolve_path(self.path)

        assert resolved is not None
        self.assertNotIn("script", resolved.robots)

    def test_blog_object_canonical_is_honoured(self):
        self.data.post.canonical_url = "https://elsewhere.example/original/"
        self.data.post.save()

        resolved = resolve_path(self.data.post.get_absolute_url())

        assert resolved is not None
        self.assertEqual(resolved.canonical, "https://elsewhere.example/original/")
        self.assertEqual(resolved.provenance["canonical"], "object")


class SchemaSafetyTests(TestCase):
    def test_json_ld_cannot_break_out_of_its_script_element(self):
        payload = to_json_ld({"name": "</script><script>alert(1)</script> & <b>"})

        self.assertNotIn("</script>", payload)
        self.assertNotIn("<", payload)
        self.assertEqual(
            json.loads(payload)["name"], "</script><script>alert(1)</script> & <b>"
        )

    @override_settings(SITE_URL="")
    def test_missing_site_url_falls_back_to_the_request_origin_with_a_visible_audit(
        self,
    ):
        response = self.client.get("/")
        head = head_of(response)

        self.assertTrue(head.canonicals[0].startswith("http://testserver/"))


class SeoDisabledTests(TestCase):
    @override_settings(SITE_URL=SITE)
    def test_disabling_schema_removes_json_ld_but_keeps_metadata(self):
        row = SEOSettings.get_solo()
        row.auto_schema_enabled = False
        row.save()

        head = head_of(self.client.get("/"))

        self.assertEqual(head.jsonld_types, [])
        self.assertEqual(len(head.titles), 1)


@pytest.mark.parametrize(
    "path", ["/", "/firmwares/brands/", "/blog/", "/tags/", "/pages/"]
)
@pytest.mark.django_db
def test_head_markup_contains_no_inline_script_or_style(client, path, settings):
    settings.SITE_URL = SITE
    html = client.get(path).content.decode()
    head = html.split("</head>")[0]

    assert "<style" not in head
    assert "onload=" not in head
    for script in head.split("<script")[1:]:
        assert (
            'type="application/ld+json"' in script.split(">")[0]
            or "src=" in script.split(">")[0]
        )
