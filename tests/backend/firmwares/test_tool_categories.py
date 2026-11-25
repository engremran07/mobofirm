"""Generated flashing tool and guide categories (vendor, family, brand, OS).

Covers the sync service and its signals, derived tool/guide membership, the public
display rule (only categories with an active tool or published guide), the staff
console list and the seeded official OEM tools.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sitemaps import Sitemap
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.blog.models import Post, PostStatus
from apps.firmwares.chipsets import SUPPORTED_CHIPSET_VENDORS, create_chipset_record
from apps.firmwares.models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
)
from apps.firmwares.seed_data import TOOLS
from apps.firmwares.sitemaps import FirmwareToolCategoriesSitemap
from apps.firmwares.tool_categories import (
    OS_TYPES,
    VENDOR_SPECS,
    CategoryIndex,
    deactivate_brand_category,
    ensure_brand_category,
    sync_reference_categories,
)

Kind = FlashingToolCategory.Kind
pytestmark = pytest.mark.django_db


@pytest.fixture(name="legacy")
def legacy_category():
    return FlashingToolCategory.objects.create(name="Legacy", slug="legacy")


def make_tool(legacy, slug, **extra):
    chipsets = extra.pop("chipsets", ())
    brands = extra.pop("brands", ())
    tool = FlashingTool.objects.create(
        category=legacy, name=slug.title(), slug=slug, **extra
    )
    tool.chipsets.add(*chipsets)
    tool.supported_brands.add(*brands)
    return tool


def make_chipset(vendor, family, name):
    return create_chipset_record(
        vendor=vendor,
        family=family,
        name=name,
        slug=slugify(f"{vendor} {name}"),
    )


def publish_guide(brand, model=None, title="Guide"):
    author = get_user_model().objects.create_user(
        username=f"author-{title}-{brand.slug}", email=f"{title}-{brand.slug}@x.test"
    )
    post = Post.objects.create(title=title, body="<p>Body</p>", author=author)
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )
    template = FlashingGuideTemplate.objects.create(
        title_template=title,
        body_template="{steps}",
        guide_type=FlashingGuideTemplate.GuideType.STOCK_FLASH,
        brand=brand,
    )
    return GeneratedFlashingGuide.objects.create(
        template=template, post=post, brand=brand, model=model
    )


class TestSync:
    def test_one_category_per_allow_listed_vendor_with_families_as_children(self):
        report = sync_reference_categories()

        vendors = FlashingToolCategory.objects.filter(kind=Kind.CHIPSET_VENDOR)
        assert {cat.vendor for cat in vendors} == set(SUPPORTED_CHIPSET_VENDORS)
        assert {spec.vendor for spec in VENDOR_SPECS} == set(SUPPORTED_CHIPSET_VENDORS)
        mediatek = vendors.get(vendor="MediaTek")
        assert {c.family for c in mediatek.children.all()} == {
            "Helio",
            "Dimensity",
            "Kompanio",
        }
        assert {c.name for c in vendors.get(vendor="Unisoc").children.all()} == {
            "Unisoc T-series",
            "Unisoc Spreadtrum",
            "Unisoc RDA",
        }
        assert vendors.get(vendor="Apple").children.count() == 2
        assert vendors.get(vendor="Qualcomm").children.get().family == "Snapdragon"
        generated_kinds = (
            Kind.CHIPSET_VENDOR,
            Kind.CHIPSET_FAMILY,
            Kind.OS_TYPE,
            Kind.BRAND,
        )
        assert all(
            category.is_auto
            for category in FlashingToolCategory.objects.filter(
                kind__in=generated_kinds
            )
        )
        assert (
            report.created
            == FlashingToolCategory.objects.filter(kind__in=generated_kinds).count()
        )

    def test_os_categories_are_only_the_supported_systems(self):
        sync_reference_categories()
        keys = set(
            FlashingToolCategory.objects.filter(kind=Kind.OS_TYPE).values_list(
                "os_key", flat=True
            )
        )
        assert keys == {key for key, *_ in OS_TYPES} == {"android", "ios"}

    def test_sync_is_idempotent_and_keeps_staff_edits(self):
        sync_reference_categories()
        category = FlashingToolCategory.objects.get(slug="chipsets-mediatek")
        category.description = "Edited by staff"
        category.save()
        before = FlashingToolCategory.objects.count()

        report = sync_reference_categories()

        assert FlashingToolCategory.objects.count() == before
        assert report.created == 0 and report.updated == 0
        category.refresh_from_db()
        assert category.description != "" and category.is_auto

    def test_legacy_categories_keep_working(self, legacy):
        sync_reference_categories()
        legacy.refresh_from_db()
        assert (legacy.kind, legacy.is_auto, legacy.parent) == (
            Kind.GENERAL,
            False,
            None,
        )

    def test_a_name_taken_by_a_manual_category_gets_a_suffix(self):
        FlashingToolCategory.objects.create(name="MediaTek chipsets", slug="manual-mt")
        sync_reference_categories()
        auto = FlashingToolCategory.objects.get(slug="chipsets-mediatek")
        assert auto.name == "MediaTek chipsets (2)"

    def test_the_seed_command_is_additive(self, capsys):
        call_command("seed_tool_categories", "--confirm")
        first = FlashingToolCategory.objects.count()
        call_command("seed_tool_categories", "--confirm")
        assert FlashingToolCategory.objects.count() == first
        assert "created" in capsys.readouterr().out


class TestBrandCategories:
    def test_saving_a_brand_creates_its_category_without_posts_or_shares(self):
        brand = Brand.objects.create(name="Acme", slug="acme")

        category = FlashingToolCategory.objects.get(kind=Kind.BRAND, brand=brand)
        assert (category.slug, category.name, category.is_auto) == (
            "brand-acme",
            "Acme",
            True,
        )
        assert Post.objects.filter(title__icontains="tool categor").count() == 0

    def test_renaming_a_brand_keeps_the_category_and_the_old_slug_redirects(self):
        brand = Brand.objects.create(name="Acme", slug="acme")
        category = FlashingToolCategory.objects.get(brand=brand)
        tool = make_tool(
            FlashingToolCategory.objects.create(name="L", slug="l"), "acme-tool"
        )
        tool.supported_brands.add(brand)

        brand.name, brand.slug = "Acme Mobile", "acme-mobile"
        brand.save()

        category.refresh_from_db()
        assert (category.slug, category.name) == ("brand-acme-mobile", "Acme Mobile")
        assert category.slug_history == ["brand-acme"]
        response = Client().get(
            reverse("firmwares:flashing_category", kwargs={"slug": "brand-acme"})
        )
        assert response.status_code == 301
        assert response["Location"] == category.get_absolute_url()
        assert FlashingToolCategory.objects.filter(kind=Kind.BRAND).count() == 1

    def test_deleting_a_brand_deactivates_its_category_and_a_new_one_reuses_it(self):
        brand = Brand.objects.create(name="Acme", slug="acme")
        category = FlashingToolCategory.objects.get(brand=brand)

        brand.delete()

        category.refresh_from_db()
        assert category.is_active is False and category.brand_id is None
        again = Brand.objects.create(name="Acme", slug="acme")
        category.refresh_from_db()
        assert (category.brand_id, category.is_active) == (again.pk, True)
        assert FlashingToolCategory.objects.filter(kind=Kind.BRAND).count() == 1

    def test_deactivate_helper_never_deletes(self):
        brand = Brand.objects.create(name="Acme", slug="acme")
        assert deactivate_brand_category(brand) == 1
        assert FlashingToolCategory.objects.filter(brand=brand).exists()
        ensure_brand_category(brand)
        assert FlashingToolCategory.objects.filter(kind=Kind.BRAND).count() == 1

    def test_a_chipset_of_a_supported_vendor_creates_its_vendor_categories(self):
        make_chipset("Qualcomm", "Snapdragon", "Snapdragon 680")
        assert FlashingToolCategory.objects.filter(
            kind=Kind.CHIPSET_VENDOR, vendor="Qualcomm"
        ).exists()


class TestMembership:
    def test_a_tool_belongs_to_the_categories_of_its_chipsets_brands_and_os(
        self, legacy
    ):
        sync_reference_categories()
        brand = Brand.objects.create(name="Acme", slug="acme")
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        tool = make_tool(legacy, "mtk-tool", chipsets=[chipset], brands=[brand])

        index = CategoryIndex.build()

        def ids(slug):
            return index.tool_ids(FlashingToolCategory.objects.get(slug=slug))

        assert tool.pk in ids("chipsets-mediatek")
        assert tool.pk in ids("chipsets-mediatek-helio")
        assert tool.pk not in ids("chipsets-mediatek-dimensity")
        assert tool.pk in ids("brand-acme")
        assert tool.pk in ids("os-android") and tool.pk not in ids("os-ios")
        assert tool.pk in ids("legacy")

    def test_apple_tools_belong_to_ios(self, legacy):
        sync_reference_categories()
        apple = Brand.objects.create(name="Apple", slug="apple")
        tool = make_tool(legacy, "finder", brands=[apple])
        index = CategoryIndex.build()
        ios = FlashingToolCategory.objects.get(slug="os-ios")
        android = FlashingToolCategory.objects.get(slug="os-android")
        assert tool.pk in index.tool_ids(ios)
        assert tool.pk not in index.tool_ids(android)

    def test_inactive_tools_are_not_members(self, legacy):
        sync_reference_categories()
        make_tool(legacy, "off", is_active=False)
        index = CategoryIndex.build()
        assert (
            index.tool_ids(FlashingToolCategory.objects.get(slug="os-android")) == set()
        )

    def test_published_guides_join_brand_vendor_and_os_categories(self):
        sync_reference_categories()
        apple = Brand.objects.create(name="Apple", slug="apple")
        guide = publish_guide(apple)
        draft_brand = Brand.objects.create(name="Draft", slug="draft")
        draft = publish_guide(draft_brand, title="Draft")
        Post.objects.filter(pk=draft.post_id).update(status=PostStatus.DRAFT)

        index = CategoryIndex.build()

        def guides(slug):
            return index.guide_ids(FlashingToolCategory.objects.get(slug=slug))

        assert guide.pk in guides("brand-apple")
        assert guide.pk in guides("chipsets-apple")
        assert guide.pk in guides("os-ios") and guide.pk not in guides("os-android")
        assert draft.pk not in guides("brand-draft")
        assert draft.pk not in guides("os-android")

    def test_a_model_chipset_decides_the_guide_vendor(self):
        sync_reference_categories()
        brand = Brand.objects.create(name="Acme", slug="acme")
        chipset = make_chipset("Qualcomm", "Snapdragon", "Snapdragon 680")
        model = Model.objects.create(
            brand=brand, name="Phone", slug="phone", chipset_ref=chipset
        )
        guide = publish_guide(brand, model)
        index = CategoryIndex.build()
        family = FlashingToolCategory.objects.get(slug="chipsets-qualcomm-snapdragon")
        assert guide.pk in index.guide_ids(family)


class TestPublicDisplay:
    def test_hub_lists_only_categories_with_content_and_hides_empty_ones(self, legacy):
        sync_reference_categories()
        brand = Brand.objects.create(name="Acme", slug="acme")
        Brand.objects.create(name="Lonely", slug="lonely")
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "mtk-tool", chipsets=[chipset], brands=[brand])

        html = Client().get(reverse("firmwares:flashing_tools")).content.decode()

        assert "data-browse-categories" in html
        assert 'href="/firmwares/tools/category/chipsets-mediatek/"' in html
        assert 'href="/firmwares/tools/category/chipsets-mediatek-helio/"' in html
        assert 'href="/firmwares/tools/category/brand-acme/"' in html
        assert 'href="/firmwares/tools/category/os-android/"' in html
        assert "chipsets-mediatek-dimensity" not in html
        assert "chipsets-qualcomm" not in html
        assert "brand-lonely" not in html
        assert "category/os-ios" not in html

    def test_the_hub_has_no_browse_block_without_any_content(self):
        sync_reference_categories()
        html = Client().get(reverse("firmwares:flashing_tools")).content.decode()
        assert "data-browse-categories" not in html

    def test_an_empty_category_returns_404(self, legacy):
        sync_reference_categories()
        make_tool(legacy, "t")
        response = Client().get(
            reverse("firmwares:flashing_category", kwargs={"slug": "chipsets-qualcomm"})
        )
        assert response.status_code == 404

    def test_an_empty_family_redirects_to_its_listed_parent(self, legacy):
        sync_reference_categories()
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "t", chipsets=[chipset])
        response = Client().get(
            reverse(
                "firmwares:flashing_category",
                kwargs={"slug": "chipsets-mediatek-dimensity"},
            )
        )
        assert response.status_code == 302
        assert response["Location"].endswith("/chipsets-mediatek/")

    def test_a_category_page_lists_its_tools_children_and_guides(self, legacy):
        sync_reference_categories()
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "mtk-tool", chipsets=[chipset])
        make_tool(legacy, "other-tool")
        html = (
            Client()
            .get(
                reverse(
                    "firmwares:flashing_category", kwargs={"slug": "chipsets-mediatek"}
                )
            )
            .content.decode()
        )
        assert "Mtk-Tool" in html and "Other-Tool" not in html
        assert "MediaTek Helio" in html
        assert "MediaTek Dimensity" not in html

    def test_a_category_with_only_a_published_guide_is_public(self):
        sync_reference_categories()
        apple = Brand.objects.create(name="Apple", slug="apple")
        publish_guide(apple)
        response = Client().get(
            reverse("firmwares:flashing_category", kwargs={"slug": "brand-apple"})
        )
        assert response.status_code == 200
        assert "1 guide" in response.content.decode()

    def test_an_inactive_category_is_not_public(self, legacy):
        sync_reference_categories()
        make_tool(legacy, "t")
        FlashingToolCategory.objects.filter(slug="os-android").update(is_active=False)
        response = Client().get(
            reverse("firmwares:flashing_category", kwargs={"slug": "os-android"})
        )
        assert response.status_code == 404

    def test_the_guides_page_filters_by_category(self):
        sync_reference_categories()
        apple = Brand.objects.create(name="Apple", slug="apple")
        acme = Brand.objects.create(name="Acme", slug="acme")
        publish_guide(apple, title="Apple restore")
        publish_guide(acme, title="Acme flash")
        client = Client()
        html = client.get(
            reverse("firmwares:flashing_guides"), {"category": "brand-apple"}
        ).content.decode()
        assert "Apple restore" in html and "Acme flash" not in html
        assert 'name="category"' in html
        unknown = client.get(
            reverse("firmwares:flashing_guides"), {"category": "chipsets-rockchip"}
        ).content.decode()
        assert "Apple restore" in unknown and "Acme flash" in unknown

    def test_the_sitemap_lists_only_public_categories(self, legacy):
        sync_reference_categories()
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "t", chipsets=[chipset])
        sitemap = FirmwareToolCategoriesSitemap()
        assert isinstance(sitemap, Sitemap)
        urls = {sitemap.location(item) for item in sitemap.items()}
        assert "/firmwares/tools/category/chipsets-mediatek/" in urls
        assert "/firmwares/tools/category/os-android/" in urls
        assert not any("qualcomm" in url or "rockchip" in url for url in urls)

    def test_the_seo_registry_exposes_the_category_sitemap(self):
        from apps.seo.sitemap_registry import get_raw_sitemaps

        assert get_raw_sitemaps()["tool-categories"] is FirmwareToolCategoriesSitemap


class TestConsole:
    def staff_client(self):
        user = get_user_model().objects.create_superuser(
            username="cat-admin", email="cat-admin@example.test", password="x"
        )
        client = Client()
        client.force_login(user)
        return client

    def test_the_list_shows_kind_parent_auto_counts_and_the_empty_flag(self, legacy):
        sync_reference_categories()
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "mtk-tool", chipsets=[chipset])

        response = self.staff_client().get(
            reverse("admin:firmwares_flashingtoolcategory_changelist")
        )

        html = response.content.decode()
        assert response.status_code == 200
        assert "Chipset vendor" in html and "Chipset family" in html
        assert "auto" in html and "Empty" in html.title() or "empty" in html
        assert "MediaTek Helio" in html

    @pytest.mark.parametrize("value", ["empty", "used"])
    def test_the_content_filter_splits_empty_from_used(self, legacy, value):
        sync_reference_categories()
        chipset = make_chipset("MediaTek", "Helio", "Helio G99")
        make_tool(legacy, "mtk-tool", chipsets=[chipset])
        response = self.staff_client().get(
            reverse("admin:firmwares_flashingtoolcategory_changelist"),
            {"content": value},
        )
        names = {row.name for row in response.context["cl"].result_list}
        assert ("MediaTek chipsets" in names) == (value == "used")
        assert ("Qualcomm chipsets" in names) == (value == "empty")

    def test_generated_sources_are_read_only_on_the_change_form(self):
        sync_reference_categories()
        category = FlashingToolCategory.objects.get(slug="chipsets-apple")
        response = self.staff_client().get(
            reverse("admin:firmwares_flashingtoolcategory_change", args=[category.pk])
        )
        assert response.status_code == 200
        form = response.context["adminform"].form
        assert "vendor" not in form.fields and "kind" not in form.fields

    def test_the_console_nav_says_service_engineering(self):
        from apps.core.admin_nav_catalog import NAV_GROUPS

        assert "Service / Engineering" in repr(NAV_GROUPS)
        assert "'Engineering'" not in repr(NAV_GROUPS)


class TestOfficialOemTools:
    NEW = ("lenovo-motorola-lmsa", "sony-xperia-companion", "asus-flash-tool")

    def test_oem_tools_have_no_mirrors_cracks_or_bypass(self):
        for tool in TOOLS:
            text = f"{tool.name} {tool.description}".lower()
            assert tool.tool_type != FlashingTool.ToolType.CRACK
            assert "frp" not in tool.name.lower() and "bypass tool" not in text
            assert "activation lock" not in tool.name.lower()

    def test_unverified_downloads_are_stored_without_a_download_url(self):
        by_slug = {tool.slug: tool for tool in TOOLS}
        for slug in self.NEW:
            tool = by_slug[slug]
            assert tool.tool_type == FlashingTool.ToolType.OEM
            assert tool.download_url == ""
            assert tool.official_url.startswith("https://")
            assert tool.requirements and tool.instructions and tool.brands

    def test_seeding_adds_the_tools_categories_and_links_additively(self):
        call_command("seed_flashing_tools", "--confirm")
        first = FlashingTool.objects.count()
        call_command("seed_flashing_tools", "--confirm")

        assert FlashingTool.objects.count() == first == len(TOOLS)
        assert (
            FlashingToolCategory.objects.filter(kind=Kind.CHIPSET_VENDOR).count() == 9
        )
        for slug in self.NEW:
            assert FlashingTool.objects.get(slug=slug).download_url == ""
        odin = FlashingTool.objects.get(slug="odin")
        assert odin.tool_type != FlashingTool.ToolType.OEM
        assert "community" in odin.description
