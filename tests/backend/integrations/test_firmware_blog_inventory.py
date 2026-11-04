from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from apps.blog.models import Post, PostStatus
from apps.firmwares.blog_automation import FirmwareBlogService
from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)
from apps.governance.models import ReviewCase


class FirmwareBlogInventoryTests(TestCase):
    @override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=False)
    def test_new_model_creates_one_reviewable_post_after_commit(self):
        author = get_user_model().objects.create_user(
            username="automatic-firmware-author",
            email="automatic-firmware-author@example.test",
            password="test-password-for-automation",
            is_staff=True,
        )

        with (
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_brand",
                return_value={},
            ),
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_model",
                return_value={},
            ),
            patch("apps.firmwares.tasks.generate_firmware_blog_post.delay") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            brand = Brand.objects.create(
                name="Automation Brand", slug="automation-brand"
            )
            model = Model.objects.create(
                brand=brand,
                name="Automation Phone",
                slug="automation-phone",
                model_code="AB-P1",
            )

        enqueue.assert_not_called()
        post = Post.objects.get(firmware_model=model)
        self.assertEqual(post.author, author)
        self.assertEqual(post.status, PostStatus.IN_REVIEW)
        self.assertFalse(post.is_published)
        self.assertEqual(
            ReviewCase.objects.filter(
                object_id=str(post.pk),
                content_type__app_label="blog",
                content_type__model="post",
            ).count(),
            1,
        )

        duplicate = FirmwareBlogService.generate_firmware_post(
            model,
            force_update=True,
        )
        self.assertEqual(duplicate.pk, post.pk)
        self.assertEqual(Post.objects.filter(firmware_model=model).count(), 1)

    @override_settings(FIRMWARE_BLOG_AUTOGENERATION_ASYNC=True)
    def test_async_blog_generation_is_explicitly_opt_in(self):
        with (
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_brand",
                return_value={},
            ),
            patch(
                "apps.firmwares.admin_autofill.FirmwareAutoFill.autofill_model",
                return_value={},
            ),
            patch("apps.firmwares.tasks.generate_firmware_blog_post.delay") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            brand = Brand.objects.create(
                name="Queued Automation Brand",
                slug="queued-automation-brand",
            )
            model = Model.objects.create(
                brand=brand,
                name="Queued Automation Phone",
                slug="queued-automation-phone",
            )

        enqueue.assert_called_once_with(model_id=model.pk, force_update=True)

    def test_blog_inventory_displays_real_firmware_fields_and_all_reviewed_types(self):
        author = get_user_model().objects.create_user(
            username="firmware-blog-author",
            email="firmware-blog-author@example.test",
            password="test-password",
        )
        brand = Brand.objects.create(name="Catalog Brand", slug="catalog-brand")
        model = Model.objects.create(
            brand=brand,
            name="Catalog Phone",
            slug="catalog-phone",
            model_code="CB-P1",
        )
        OfficialFirmware.objects.create(
            original_file_name="catalog-release.tgz",
            stored_file_path="/firmware/catalog-release.tgz",
            build_number="CB-P1-2026.01",
            brand=brand,
            model=model,
            package_format=FirmwarePackageFormat.FASTBOOT_ROM,
            is_verified=True,
        )
        UnclassifiedFirmware.objects.create(
            original_file_name="catalog-unclassified.zip",
            stored_file_path="/firmware/catalog-unclassified.zip",
            brand=brand,
            model=model,
            is_verified=True,
        )
        post = Post.objects.create(
            title="Catalog Phone firmware notes",
            slug="catalog-phone-firmware-notes",
            body="<p>Editorial notes for the catalog phone.</p>",
            author=author,
            firmware_brand=brand,
            firmware_model=model,
        )
        Post.objects.filter(pk=post.pk).update(
            status=PostStatus.PUBLISHED,
            is_published=True,
            publish_at=timezone.now(),
            published_at=timezone.now(),
        )

        response = self.client.get(
            reverse("blog:post_detail", kwargs={"slug": post.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "catalog-release.tgz")
        self.assertContains(response, "CB-P1-2026.01")
        self.assertContains(response, "Fastboot ROM")
        self.assertContains(response, "Official")
        self.assertContains(response, "catalog-unclassified.zip")
        self.assertContains(response, "Unclassified")
