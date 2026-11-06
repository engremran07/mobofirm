from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.models import (
    AutomatedBlogLink,
    Brand,
    Chipset,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)
from apps.tags.models import Tag

NO_AUTOFILL = (
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_brand",
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_model",
    "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_variant",
)


@override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
class BlogHubTests(TestCase):
    def setUp(self):
        for target in NO_AUTOFILL:
            patcher = patch(target, return_value={})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.brand = Brand.objects.create(
            name="Hubphone", slug="hubphone", description="A brand for hub tests."
        )
        self.chipset = Chipset.objects.create(
            vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
        )
        self.model = Model.objects.create(
            brand=self.brand, name="H1", slug="h1", chipset_ref=self.chipset
        )
        self.model_post = FirmwareBlogService.sync_model_post(self.model)
        self.brand_post = FirmwareBlogService.sync_brand_post(self.brand)
        self.chipset_post = FirmwareBlogService.sync_chipset_post(self.chipset)

    def firmware(self, name, model_class=OfficialFirmware, **extra):
        return model_class.objects.create(
            original_file_name=name,
            stored_file_path=f"/firmware/{name}",
            brand=self.brand,
            model=self.model,
            is_verified=True,
            **extra,
        )

    def test_brand_hub_leads_with_the_brand_post_and_lists_model_posts(self):
        response = self.client.get(
            reverse("blog:brand_posts", kwargs={"brand_slug": self.brand.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Featured story")
        self.assertContains(response, self.brand_post.title)
        self.assertContains(response, self.model_post.title)
        self.assertContains(response, "Hubphone firmware")
        self.assertNotContains(response, "Local demo preview")

    def test_model_hub_links_to_the_chipset_hub_and_catalogue(self):
        response = self.client.get(
            reverse(
                "blog:model_posts",
                kwargs={"brand_slug": self.brand.slug, "model_slug": self.model.slug},
            )
        )

        self.assertContains(response, self.model_post.title)
        self.assertContains(
            response, reverse("blog:chipset_posts", kwargs={"slug": self.chipset.slug})
        )
        self.assertContains(
            response,
            reverse(
                "firmwares:model_detail",
                kwargs={"brand_slug": self.brand.slug, "model_slug": self.model.slug},
            ),
        )

    def test_chipset_hub_exists_and_lists_posts_for_devices_on_the_chipset(self):
        response = self.client.get(
            reverse("blog:chipset_posts", kwargs={"slug": self.chipset.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.chipset_post.title)
        self.assertContains(response, self.model_post.title)
        self.assertEqual(
            self.client.get(
                reverse("blog:chipset_posts", kwargs={"slug": "missing"})
            ).status_code,
            404,
        )

    def test_hubs_for_unknown_brands_and_models_are_404(self):
        self.assertEqual(
            self.client.get(
                reverse("blog:brand_posts", kwargs={"brand_slug": "nope"})
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "blog:model_posts",
                    kwargs={"brand_slug": self.brand.slug, "model_slug": "nope"},
                )
            ).status_code,
            404,
        )

    def test_automated_model_post_shows_the_editorial_byline_toc_and_panel(self):
        self.firmware("hub-official.zip")
        self.firmware("hub-unclassified.zip", model_class=UnclassifiedFirmware)
        FirmwareBlogService.sync_model_post(self.model)

        response = self.client.get(self.model_post.get_absolute_url())

        self.assertContains(response, "Mobofirm Editorial")
        self.assertNotContains(response, "mobofirm-automation")
        self.assertNotContains(response, "automation.invalid")
        self.assertContains(response, "hub-official.zip")
        self.assertContains(response, "In this article")
        self.assertContains(response, "Firmware at a glance")
        self.assertContains(response, "2</strong> approved files")
        self.assertContains(response, 'class="article-table" role="region"')
        self.assertContains(response, "automatically")

    def test_automated_posts_do_not_repeat_the_listing_in_a_second_inventory(self):
        self.firmware("only-once.zip")
        FirmwareBlogService.sync_model_post(self.model)

        response = self.client.get(self.model_post.get_absolute_url())

        self.assertNotContains(response, "Firmware downloads</h2>")
        self.assertEqual(response.content.decode().count("only-once.zip"), 1)

    def test_manual_model_posts_show_the_live_grouped_inventory(self):
        from django.contrib.auth import get_user_model

        author = get_user_model().objects.create_user(
            username="hub-author", email="hub-author@example.test"
        )
        manual = Post.objects.create(
            title="Hand written H1 notes",
            body="<p>Editorial notes for the H1.</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            firmware_brand=self.brand,
            firmware_model=self.model,
        )
        self.firmware("manual-official.tgz", build_number="HB-1")
        self.firmware("manual-unclassified.zip", model_class=UnclassifiedFirmware)

        response = self.client.get(manual.get_absolute_url())

        self.assertContains(response, "Firmware downloads")
        self.assertContains(response, "manual-official.tgz")
        self.assertContains(response, "HB-1")
        self.assertContains(response, "manual-unclassified.zip")
        self.assertContains(response, "Unclassified")
        self.assertContains(response, "#fw-")

    def test_catalog_tags_are_curated_and_attached_to_automated_posts(self):
        self.assertTrue(Tag.objects.filter(name="Hubphone", is_curated=True).exists())
        names = set(self.model_post.tags.values_list("name", flat=True))

        self.assertIn("Hubphone", names)
        self.assertTrue(all(tag.is_curated for tag in self.model_post.tags.all()))
        self.assertEqual(AutomatedBlogLink.objects.count(), 3)
