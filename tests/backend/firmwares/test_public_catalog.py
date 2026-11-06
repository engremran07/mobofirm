from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.firmwares.models import (
    Brand,
    Chipset,
    EngineeringFirmware,
    FirmwarePackageFormat,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
    Variant,
)
from apps.firmwares.sitemaps import FirmwareFileSitemap

NO_AUTOFILL = (
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_brand",
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_model",
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_variant",
)


class CatalogTestCase(TestCase):
    def setUp(self):
        for target in NO_AUTOFILL:
            patcher = patch(target, return_value={})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.brand = Brand.objects.create(name="Catalog Brand", slug="catalog-brand")
        self.chipset = Chipset.objects.create(
            vendor="MediaTek",
            family="Helio",
            name="Helio G99",
            slug="mediatek-helio-g99",
        )
        self.model = Model.objects.create(
            brand=self.brand,
            name="Catalog Phone",
            slug="catalog-phone",
            model_code="CP-1",
            chipset_ref=self.chipset,
            cpu="Octa-core 2x2.2 GHz",
        )

    def firmware(self, name, model_class=OfficialFirmware, **extra):
        values = {
            "original_file_name": name,
            "stored_file_path": f"/firmware/{name}",
            "brand": self.brand,
            "model": self.model,
            "is_verified": True,
        }
        values.update(extra)
        return model_class.objects.create(**values)

    def publish_guide(self, title="Public guide", status=PostStatus.PUBLISHED):
        author = get_user_model().objects.create_user(
            username=f"guide-{title.lower().replace(' ', '-')}",
            email=f"{title.lower().replace(' ', '-')}@example.test",
        )
        post = Post.objects.create(
            title=title,
            body="<p>Guide body for the catalogue.</p>",
            author=author,
            firmware_brand=self.brand,
            firmware_model=self.model,
        )
        Post.objects.filter(pk=post.pk).update(
            status=status,
            is_published=status == PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(minutes=1),
            published_at=timezone.now() - timedelta(minutes=1),
        )
        template = FlashingGuideTemplate.objects.create(
            title_template=title,
            body_template="Body {steps}",
            guide_type=FlashingGuideTemplate.GuideType.STOCK_FLASH,
            brand=self.brand,
        )
        return GeneratedFlashingGuide.objects.create(
            template=template, post=post, brand=self.brand, model=self.model
        )


class ReviewBoundaryTests(CatalogTestCase):
    def test_autocomplete_lists_only_reviewed_active_firmware_of_every_category(self):
        self.firmware("public-official.zip")
        self.firmware("public-engineering.zip", EngineeringFirmware)
        self.firmware("secret-pending.zip", is_verified=False)
        self.firmware("secret-inactive.zip", is_active=False)

        response = self.client.get(
            reverse("firmwares:api_search_autocomplete"),
            {"q": "public", "type": "firmwares", "limit": "20"},
        )
        hidden = self.client.get(
            reverse("firmwares:api_search_autocomplete"),
            {"q": "secret", "type": "firmwares"},
        )

        names = {item["name"] for item in response.json()["results"]}
        self.assertEqual(names, {"public-official.zip", "public-engineering.zip"})
        self.assertEqual(hidden.json()["results"], [])

    def test_autocomplete_finds_chipsets_and_tolerates_a_bad_limit(self):
        response = self.client.get(
            reverse("firmwares:api_search_autocomplete"),
            {"q": "helio", "type": "chipsets", "limit": "not-a-number"},
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(results[0]["type"], "chipset")
        self.assertEqual(
            results[0]["url"],
            reverse("firmwares:chipset_detail", args=[self.chipset.slug]),
        )

    def test_stats_api_hides_unreviewed_firmware(self):
        public = self.firmware("stats-public.zip")
        pending = self.firmware("stats-pending.zip", is_verified=False)

        ok = self.client.get(
            reverse("firmwares:api_firmware_stats", args=["official", public.pk])
        )
        missing = self.client.get(
            reverse("firmwares:api_firmware_stats", args=["official", pending.pk])
        )

        self.assertEqual(ok.status_code, 200)
        self.assertEqual(missing.status_code, 404)

    def test_sitemap_lists_only_public_firmware_including_unclassified(self):
        public = self.firmware("map-public.zip")
        unclassified = self.firmware("map-unclassified.zip", UnclassifiedFirmware)
        self.firmware("map-pending.zip", is_verified=False)

        urls = {
            FirmwareFileSitemap().location(item)
            for item in FirmwareFileSitemap().items()
        }

        self.assertEqual(
            urls,
            {
                reverse("firmwares:firmware_detail", args=["official", public.pk]),
                reverse(
                    "firmwares:firmware_detail", args=["unclassified", unclassified.pk]
                ),
            },
        )

    def test_firmware_without_a_brand_still_renders_its_detail_page(self):
        firmware = self.firmware("no-brand.zip", brand=None)

        response = self.client.get(
            reverse("firmwares:firmware_detail", args=["official", firmware.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no-brand.zip")


class RoutingTests(CatalogTestCase):
    def test_fixed_routes_win_over_the_brand_catch_all(self):
        self.assertEqual(resolve("/firmwares/chipsets/").url_name, "chipset_list")
        self.assertEqual(
            resolve(f"/firmwares/chipsets/{self.chipset.slug}/").url_name,
            "chipset_detail",
        )
        self.assertEqual(resolve("/firmwares/tools/").url_name, "flashing_tools")
        self.assertEqual(resolve("/firmwares/guides/").url_name, "flashing_guides")
        self.assertEqual(resolve("/firmwares/catalog-brand/").url_name, "brand_detail")

    def test_brand_slugs_cannot_shadow_fixed_routes(self):
        for slug in ("chipsets", "tools", "guides", "search", "api", "browse"):
            with self.subTest(slug=slug):
                with self.assertRaises(ValidationError):
                    Brand(name=f"Shadow {slug}", slug=slug).full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Brand.objects.create(name=f"Shadow {slug}", slug=slug)


class BrandPageTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.brand.logo_url = "/static/img/brands/catalog-brand.svg"
        self.brand.description = "A brand used in tests."
        self.brand.save()
        self.firmware("brand-official.zip")
        self.firmware("brand-engineering.zip", EngineeringFirmware)
        self.firmware("brand-pending.zip", is_verified=False)
        category = FlashingToolCategory.objects.create(
            name="Brand tools", slug="brand-tools"
        )
        self.tool = FlashingTool.objects.create(
            category=category, name="Brand Flash Tool", slug="brand-flash-tool"
        )
        self.tool.supported_brands.add(self.brand)
        self.publish_guide("Published brand guide")
        self.publish_guide("Draft brand guide", PostStatus.DRAFT)

    def test_brand_page_shows_logo_categories_chipsets_tools_guides_and_links(self):
        response = self.client.get(
            reverse("firmwares:brand_detail", kwargs={"slug": self.brand.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/img/brands/catalog-brand.svg")
        self.assertContains(response, "Official</span>")
        self.assertContains(response, "Engineering</span>")
        self.assertContains(response, self.chipset.display_name)
        self.assertContains(
            response, reverse("firmwares:chipset_detail", args=[self.chipset.slug])
        )
        self.assertContains(response, "Brand Flash Tool")
        self.assertContains(response, "Published brand guide")
        self.assertNotContains(response, "Draft brand guide")
        self.assertContains(
            response,
            reverse("blog:brand_posts", kwargs={"brand_slug": self.brand.slug}),
        )
        self.assertEqual(response.context["total_firmwares"], 2)
        self.assertEqual(response.context["models"][0].firmware_count, 2)

    def test_external_logo_urls_are_not_rendered_because_csp_would_block_them(self):
        Brand.objects.filter(pk=self.brand.pk).update(
            logo_url="https://cdn.example.test/logo.svg"
        )

        response = self.client.get(
            reverse("firmwares:brand_detail", kwargs={"slug": self.brand.slug})
        )

        self.assertNotContains(response, "cdn.example.test")

    def test_models_are_paginated(self):
        for index in range(30):
            Model.objects.create(
                brand=self.brand, name=f"Extra {index:02d}", slug=f"extra-{index:02d}"
            )
        url = reverse("firmwares:brand_detail", kwargs={"slug": self.brand.slug})

        first = self.client.get(url)
        second = self.client.get(url, {"page": 2})

        self.assertEqual(first.context["model_total"], 31)
        self.assertEqual(len(first.context["models"]), 24)
        self.assertEqual(len(second.context["models"]), 7)
        self.assertContains(first, "Page 1 of 2")

    def test_brand_list_query_count_does_not_grow_with_brands(self):
        def count_queries():
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse("firmwares:brand_list"))
            self.assertEqual(response.status_code, 200)
            return len(queries)

        count_queries()  # warm the shared site-settings and page-link caches
        baseline = count_queries()
        for index in range(8):
            brand = Brand.objects.create(name=f"Scale {index}", slug=f"scale-{index}")
            model = Model.objects.create(brand=brand, name="M", slug="m")
            OfficialFirmware.objects.create(
                original_file_name=f"scale-{index}.zip",
                stored_file_path=f"/f/scale-{index}.zip",
                brand=brand,
                model=model,
                is_verified=True,
            )

        self.assertEqual(count_queries(), baseline)

    def test_brand_detail_query_count_does_not_grow_with_models(self):
        url = reverse("firmwares:brand_detail", kwargs={"slug": self.brand.slug})

        def count_queries():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(queries)

        count_queries()  # warm the shared site-settings and page-link caches
        baseline = count_queries()
        for index in range(10):
            Model.objects.create(
                brand=self.brand,
                name=f"Scaled {index}",
                slug=f"scaled-{index}",
                chipset_ref=self.chipset,
            )

        self.assertEqual(count_queries(), baseline)


class ChipsetPageTests(CatalogTestCase):
    def test_chipset_list_and_detail_pages(self):
        other_brand = Brand.objects.create(name="Other Chip Brand", slug="other-chip")
        other_model = Model.objects.create(
            brand=other_brand, name="Other", slug="other", chipset="MediaTek Helio G99"
        )
        variant_model = Model.objects.create(
            brand=other_brand, name="Variant Host", slug="variant-host"
        )
        Variant.objects.create(
            model=variant_model,
            name="Global",
            slug="global",
            chipset="MediaTek Helio G99",
        )
        self.firmware("chip-official.zip")
        self.firmware("chip-hidden.zip", is_verified=False)
        OfficialFirmware.objects.create(
            original_file_name="chip-other.zip",
            stored_file_path="/f/chip-other.zip",
            brand=other_brand,
            model=other_model,
            is_verified=True,
        )
        category = FlashingToolCategory.objects.create(
            name="Chip tools", slug="chip-tools"
        )
        tool = FlashingTool.objects.create(
            category=category, name="Helio Flasher", slug="helio-flasher"
        )
        tool.chipsets.add(self.chipset)
        self.publish_guide("Helio guide")

        listing = self.client.get(reverse("firmwares:chipset_list"))
        detail = self.client.get(
            reverse("firmwares:chipset_detail", kwargs={"slug": self.chipset.slug})
        )

        self.assertContains(listing, "MediaTek Helio G99")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Catalog Phone")
        self.assertContains(detail, "Other")
        self.assertContains(detail, "Variant Host")
        self.assertContains(detail, "Helio Flasher")
        self.assertContains(detail, "Helio guide")
        self.assertEqual(detail.context["total_firmwares"], 2)
        self.assertEqual(
            {brand.slug for brand in detail.context["brands"]},
            {"catalog-brand", "other-chip"},
        )

    def test_unknown_chipset_is_a_404_and_search_filters_the_list(self):
        Chipset.objects.create(
            vendor="Qualcomm", name="Snapdragon 680", slug="qualcomm-snapdragon-680"
        )

        missing = self.client.get(
            reverse("firmwares:chipset_detail", kwargs={"slug": "nope"})
        )
        filtered = self.client.get(
            reverse("firmwares:chipset_list"), {"q": "snapdragon"}
        )

        self.assertEqual(missing.status_code, 404)
        self.assertContains(filtered, "Snapdragon 680")
        self.assertNotContains(filtered, "Helio G99")


class BrowseTests(CatalogTestCase):
    def test_browse_paginates_across_categories_newest_first(self):
        for index in range(13):
            self.firmware(f"page-official-{index:02d}.zip")
        for index in range(12):
            self.firmware(f"page-engineering-{index:02d}.zip", EngineeringFirmware)
        url = reverse("firmwares:browse")

        first = self.client.get(url)
        second = self.client.get(url, {"page": 2})

        self.assertEqual(first.context["firmwares"].paginator.count, 25)
        self.assertEqual(len(first.context["firmwares"]), 20)
        self.assertEqual(len(second.context["firmwares"]), 5)
        stamps = [fw.created_at for fw in first.context["firmwares"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        types = {fw.firmware_type for fw in first.context["firmwares"]}
        self.assertEqual(types, {"official", "engineering"})

    def test_browse_does_not_load_every_record(self):
        for index in range(30):
            self.firmware(f"bounded-{index:02d}.zip")

        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("firmwares:browse"))

        self.assertLess(len(queries), 25)

    def test_browse_filters_by_chipset_slug_type_and_package_format(self):
        match = self.firmware(
            "filter-match.zip", package_format=FirmwarePackageFormat.FULL_OTA
        )
        other_model = Model.objects.create(
            brand=self.brand, name="No Chip", slug="no-chip"
        )
        OfficialFirmware.objects.create(
            original_file_name="filter-other.zip",
            stored_file_path="/f/filter-other.zip",
            brand=self.brand,
            model=other_model,
            is_verified=True,
            package_format=FirmwarePackageFormat.FULL_OTA,
        )

        response = self.client.get(
            reverse("firmwares:browse"),
            {
                "chipset": self.chipset.slug,
                "type": "official",
                "package_format": "full_ota",
            },
        )

        self.assertEqual([fw.pk for fw in response.context["firmwares"]], [match.pk])
        self.assertContains(response, "MediaTek Helio G99")

    def test_invalid_type_is_a_404(self):
        self.assertEqual(
            self.client.get(reverse("firmwares:browse"), {"type": "bogus"}).status_code,
            404,
        )


class ModelPageTests(CatalogTestCase):
    def test_model_page_renders_specifications_chipset_and_grouped_firmware(self):
        self.firmware("model-official.zip")
        self.firmware("model-engineering.zip", EngineeringFirmware)
        self.firmware("model-hidden.zip", is_verified=False)
        self.publish_guide("Model guide")

        response = self.client.get(
            reverse(
                "firmwares:model_detail",
                kwargs={"brand_slug": self.brand.slug, "model_slug": self.model.slug},
            )
        )

        self.assertContains(response, "Octa-core 2x2.2 GHz")
        self.assertContains(
            response, reverse("firmwares:chipset_detail", args=[self.chipset.slug])
        )
        self.assertContains(response, "model-official.zip")
        self.assertContains(response, "Official firmware")
        self.assertContains(response, "Engineering firmware")
        self.assertContains(response, "Model guide")
        self.assertEqual(response.context["total_firmwares"], 2)
        self.assertNotContains(response, "model-hidden.zip")


class ToolsAndGuidesHubTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        category = FlashingToolCategory.objects.create(name="Hub", slug="hub")
        self.tool = FlashingTool.objects.create(
            category=category, name="Hub Tool", slug="hub-tool", is_featured=True
        )
        self.tool.chipsets.add(self.chipset)
        self.guide = self.publish_guide("Live hub guide")
        self.draft = self.publish_guide("Draft hub guide", PostStatus.DRAFT)
        self.guide.template.recommended_tools.add(self.tool)

    def test_hub_lists_tools_and_only_published_guides(self):
        response = self.client.get(reverse("firmwares:flashing_tools"))

        self.assertContains(response, "Hub Tool")
        self.assertContains(response, "Live hub guide")
        self.assertNotContains(response, "Draft hub guide")

    def test_guides_page_filters_and_hides_drafts(self):
        url = reverse("firmwares:flashing_guides")

        listing = self.client.get(url)
        filtered = self.client.get(url, {"type": "frp_bypass"})
        by_brand = self.client.get(url, {"brand": self.brand.slug})

        self.assertContains(listing, "Live hub guide")
        self.assertNotContains(listing, "Draft hub guide")
        self.assertNotContains(filtered, "Live hub guide")
        self.assertContains(by_brand, "Live hub guide")

    def test_tool_detail_shows_chipsets_and_never_leaks_draft_guides(self):
        response = self.client.get(
            reverse("firmwares:flashing_tool_detail", kwargs={"slug": self.tool.slug})
        )

        self.assertContains(response, "MediaTek Helio G99")
        self.assertContains(response, "Live hub guide")
        self.assertNotContains(response, "Draft hub guide")
