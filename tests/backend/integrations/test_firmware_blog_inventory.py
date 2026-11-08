from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.firmwares.blog_automation import FirmwareBlogService, get_automation_author
from apps.firmwares.models import (
    AutomatedBlogLink,
    Brand,
    Chipset,
    EngineeringFirmware,
    FirmwarePackageFormat,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
    Variant,
)
from apps.firmwares.tasks import catchup_missing_blog_posts


class CatalogTestCase(TestCase):
    def commit(self):
        return self.captureOnCommitCallbacks(execute=True)

    def make_model(self, *, brand_slug="auto-brand", slug="auto-phone", **extra):
        brand, _ = Brand.objects.get_or_create(
            slug=brand_slug, defaults={"name": brand_slug.replace("-", " ").title()}
        )
        return Model.objects.create(
            brand=brand, name=slug.replace("-", " ").title(), slug=slug, **extra
        )

    def make_firmware(self, model, name, *, model_class=OfficialFirmware, **extra):
        values = {
            "original_file_name": name,
            "stored_file_path": f"/firmware/{name}",
            "brand": model.brand,
            "model": model,
            "is_verified": True,
        }
        values.update(extra)
        return model_class.objects.create(**values)


@override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
class AutomatedModelBlogTests(CatalogTestCase):
    def test_new_model_creates_exactly_one_published_post_by_the_system_author(self):
        self.assertFalse(get_user_model().objects.filter(is_staff=True).exists())

        with self.commit():
            model = self.make_model()

        post = Post.objects.get(firmware_model=model)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertTrue(post.is_published)
        self.assertTrue(post.is_ai_generated)
        self.assertEqual(post.author, get_automation_author())
        self.assertEqual(
            post.author.username,
            getattr(
                settings, "FIRMWARE_BLOG_AUTOMATION_USERNAME", "mobofirm-automation"
            ),
        )
        self.assertFalse(post.author.is_staff)
        self.assertFalse(post.author.has_usable_password())
        self.assertEqual(
            AutomatedBlogLink.objects.filter(kind="model", model=model).count(), 1
        )

        again = FirmwareBlogService.sync_model_post(model)
        with self.commit():
            model.description = "Updated description"
            model.save()
        self.assertEqual(again.pk, post.pk)
        self.assertEqual(Post.objects.filter(firmware_model=model).count(), 1)

    def test_firmware_additions_update_the_same_post_grouped_by_every_category(self):
        with self.commit():
            model = self.make_model()
        post = Post.objects.get(firmware_model=model)
        self.assertIn("No approved firmware files", post.body)

        with self.commit():
            official = self.make_firmware(model, "auto-official.zip", build_number="B1")
            engineering = self.make_firmware(
                model, "auto-engineering.zip", model_class=EngineeringFirmware
            )
            hidden = self.make_firmware(model, "auto-hidden.zip", is_verified=False)

        post.refresh_from_db()
        self.assertEqual(Post.objects.filter(firmware_model=model).count(), 1)
        self.assertEqual(Post.objects.get(firmware_model=model).pk, post.pk)
        self.assertIn("Official firmware (1)", post.body)
        self.assertIn("Engineering firmware (1)", post.body)
        self.assertIn("auto-official.zip", post.body)
        self.assertIn("auto-engineering.zip", post.body)
        self.assertNotIn(hidden.original_file_name, post.body)
        for firmware, kind in ((official, "official"), (engineering, "engineering")):
            self.assertIn(
                reverse(
                    "firmwares:firmware_download",
                    kwargs={"firmware_type": kind, "firmware_id": firmware.pk},
                ),
                post.body,
            )

        with self.commit():
            hidden.is_verified = True
            hidden.save(update_fields=["is_verified"])
        post.refresh_from_db()
        self.assertIn("auto-hidden.zip", post.body)

        with self.commit():
            engineering.delete()
        post.refresh_from_db()
        self.assertNotIn("auto-engineering.zip", post.body)

    def test_external_firmware_is_labelled_in_the_post(self):
        with self.commit():
            model = self.make_model()
            self.make_firmware(
                model,
                "external-build.zip",
                source_type=FirmwareSourceType.EXTERNAL,
                stored_file_path="",
                external_url="https://downloads.example.invalid/external-build.zip",
            )

        post = Post.objects.get(firmware_model=model)
        self.assertIn("Download (external host)", post.body)
        self.assertNotIn("downloads.example.invalid", post.body)

    def test_staff_archived_post_is_not_republished_by_later_syncs(self):
        with self.commit():
            model = self.make_model()
        post = Post.objects.get(firmware_model=model)
        post.status = PostStatus.ARCHIVED
        post.save(update_fields=["status", "is_published", "updated_at"])

        with self.commit():
            self.make_firmware(model, "after-archive.zip")

        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.ARCHIVED)
        self.assertIn("after-archive.zip", post.body)

    def test_slug_collision_with_a_manual_post_does_not_claim_it(self):
        author = get_user_model().objects.create_user(
            username="manual-writer", email="manual-writer@example.test"
        )
        manual = Post.objects.create(
            title="Manual article",
            slug="auto-brand-auto-phone-firmware-download",
            body="<p>Hand written article about the phone.</p>",
            author=author,
        )

        with self.commit():
            model = self.make_model()

        automated = Post.objects.get(firmware_model=model)
        self.assertNotEqual(automated.pk, manual.pk)
        manual.refresh_from_db()
        self.assertFalse(manual.is_ai_generated)
        self.assertEqual(manual.author, author)


@override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
class AutomatedBrandAndChipsetBlogTests(CatalogTestCase):
    def test_brand_post_lists_firmware_by_category_and_updates_in_place(self):
        with self.commit():
            model = self.make_model()
        brand_post = Post.objects.get(
            firmware_brand=model.brand, firmware_model__isnull=True
        )
        self.assertEqual(brand_post.author, get_automation_author())
        self.assertEqual(brand_post.status, PostStatus.PUBLISHED)

        with self.commit():
            self.make_firmware(model, "brand-official.zip")
            self.make_firmware(
                model, "brand-unclassified.zip", model_class=UnclassifiedFirmware
            )

        brand_post.refresh_from_db()
        self.assertEqual(
            Post.objects.filter(
                firmware_brand=model.brand, firmware_model__isnull=True
            ).count(),
            1,
        )
        self.assertIn("Official firmware (1)", brand_post.body)
        self.assertIn("Unclassified firmware (1)", brand_post.body)
        self.assertIn("brand-official.zip", brand_post.body)

    def test_chipset_post_is_created_and_refreshed_for_firmware_on_its_devices(self):
        with self.commit():
            model = self.make_model(chipset="MediaTek Helio G100 Ultra (6nm)")
        chipset = Chipset.objects.get(slug="mediatek-helio-g100-ultra")
        link = AutomatedBlogLink.objects.get(kind="chipset", chipset=chipset)
        self.assertEqual(link.post.status, PostStatus.PUBLISHED)
        self.assertEqual(link.post.author, get_automation_author())
        self.assertNotIn("chipset-build.zip", link.post.body)

        with self.commit():
            self.make_firmware(model, "chipset-build.zip")

        link.post.refresh_from_db()
        self.assertIn("chipset-build.zip", link.post.body)
        self.assertIn("Official firmware (1)", link.post.body)
        self.assertEqual(
            AutomatedBlogLink.objects.filter(kind="chipset", chipset=chipset).count(), 1
        )

    def test_variant_chipset_feeds_the_chipset_post(self):
        with self.commit():
            model = self.make_model()
            variant = Variant.objects.create(
                model=model,
                name="Global",
                slug="global",
                chipset="Qualcomm Snapdragon 680",
            )
            self.make_firmware(model, "variant-build.zip", variant=variant)

        chipset = Chipset.objects.get(vendor="Qualcomm")
        link = AutomatedBlogLink.objects.get(kind="chipset", chipset=chipset)
        self.assertIn("variant-build.zip", link.post.body)


class AutomationDispatchTests(CatalogTestCase):
    @override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=True)
    def test_async_generation_is_explicitly_opt_in(self):
        with (
            patch("apps.firmwares.tasks.sync_automated_blogs.delay") as enqueue,
            self.commit(),
        ):
            model = self.make_model()

        enqueue.assert_called()
        queued_models = {
            model_id
            for call in enqueue.call_args_list
            for model_id in call.kwargs["model_ids"]
        }
        self.assertIn(model.pk, queued_models)
        self.assertFalse(Post.objects.filter(firmware_model=model).exists())

    @override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
    def test_model_reassignment_refreshes_both_models(self):
        with self.commit():
            original = self.make_model(slug="original-phone")
            replacement = self.make_model(slug="replacement-phone")
            firmware = self.make_firmware(original, "moving.zip")
        self.assertIn("moving.zip", Post.objects.get(firmware_model=original).body)

        with self.commit():
            firmware.model = replacement
            firmware.save(update_fields=["model"])

        self.assertNotIn("moving.zip", Post.objects.get(firmware_model=original).body)
        self.assertIn("moving.zip", Post.objects.get(firmware_model=replacement).body)

    @override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
    def test_catch_up_task_and_command_create_missing_posts_idempotently(self):
        model = self.make_model(chipset="Unisoc T618")
        self.make_firmware(model, "catchup.zip")
        self.assertFalse(Post.objects.filter(is_ai_generated=True).exists())

        result = catchup_missing_blog_posts.run()

        self.assertEqual(result["errors"], 0)
        self.assertEqual(
            (result["brands"], result["models"], result["chipsets"]), (1, 1, 1)
        )
        post_count = Post.objects.count()
        self.assertEqual(post_count, 3)
        self.assertIn("catchup.zip", Post.objects.get(firmware_model=model).body)

        output = StringIO()
        call_command("sync_model_blogs", stdout=output)
        call_command("sync_model_blogs", "--model", model.slug, stdout=output)

        self.assertEqual(Post.objects.count(), post_count)
        self.assertEqual(AutomatedBlogLink.objects.count(), 3)
        self.assertIn("Blog sync finished", output.getvalue())


class BlogInventoryRenderingTests(CatalogTestCase):
    def publish(self, post):
        Post.objects.filter(pk=post.pk).update(
            status=PostStatus.PUBLISHED,
            is_published=True,
            publish_at=timezone.now(),
            published_at=timezone.now(),
        )

    def test_blog_inventory_displays_real_firmware_fields_and_all_reviewed_types(self):
        author = get_user_model().objects.create_user(
            username="firmware-blog-author", email="firmware-blog-author@example.test"
        )
        model = self.make_model(brand_slug="catalog-brand", slug="catalog-phone")
        self.make_firmware(
            model,
            "catalog-release.tgz",
            build_number="CB-P1-2026.01",
            package_format=FirmwarePackageFormat.FASTBOOT_ROM,
        )
        self.make_firmware(
            model, "catalog-unclassified.zip", model_class=UnclassifiedFirmware
        )
        post = Post.objects.create(
            title="Catalog Phone firmware notes",
            slug="catalog-phone-firmware-notes",
            body="<p>Editorial notes for the catalog phone.</p>",
            author=author,
            firmware_brand=model.brand,
            firmware_model=model,
        )
        self.publish(post)

        response = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": post.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "catalog-release.tgz")
        self.assertContains(response, "CB-P1-2026.01")
        self.assertContains(response, "Fastboot ROM")
        self.assertContains(response, "catalog-unclassified.zip")
        self.assertContains(response, "Unclassified")

    def test_model_blog_shows_inventory_empty_state_without_public_firmware(self):
        author = get_user_model().objects.create_user(
            username="empty-firmware-blog-author",
            email="empty-firmware-blog-author@example.test",
        )
        model = self.make_model(brand_slug="empty-catalog", slug="empty-phone")
        post = Post.objects.create(
            title="Empty Catalog Phone firmware inventory",
            slug="empty-catalog-phone-firmware-inventory",
            body="<p>Firmware inventory for this model.</p>",
            author=author,
            firmware_brand=model.brand,
            firmware_model=model,
        )
        self.publish(post)

        response = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": post.slug})
        )
        self.assertContains(response, "No public firmware records are available.")

        self.make_firmware(model, "added-after-publication.zip")
        updated = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": post.slug})
        )
        self.assertContains(updated, "added-after-publication.zip")
