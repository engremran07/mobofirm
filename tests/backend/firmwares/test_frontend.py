from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.blog.models import Post, PostStatus
from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    FirmwareSourceType,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)
from apps.firmwares.references import ref_kwargs
from apps.firmwares.tracking_models import FirmwareLinkReport


class FirmwareFrontendTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Northstar", slug="northstar")
        self.model = Model.objects.create(
            brand=self.brand,
            name="Atlas One",
            slug="atlas-one",
            model_code="NS-A1",
            codename="atlas",
        )

    def create_firmware(self, name: str, *, is_verified: bool) -> OfficialFirmware:
        return OfficialFirmware.objects.create(
            original_file_name=name,
            stored_file_path=f"/firmware/{name}",
            brand=self.brand,
            model=self.model,
            is_verified=is_verified,
        )

    def create_external_firmware(
        self, name: str = "external-build.zip"
    ) -> OfficialFirmware:
        return OfficialFirmware.objects.create(
            original_file_name=name,
            source_type=FirmwareSourceType.EXTERNAL,
            stored_file_path="",
            external_url="https://downloads.example.invalid/firmware.zip",
            brand=self.brand,
            model=self.model,
            is_verified=True,
        )

    def test_homepage_renders_live_catalog_data(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Northstar")
        self.assertContains(response, "Atlas One")

    def test_universal_search_returns_an_htmx_fragment(self):
        response = self.client.get(
            reverse("firmwares:universal_search"),
            {"q": "atlas"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atlas One")
        self.assertNotContains(response, "<!doctype html>")

    def test_firmware_browse_excludes_unreviewed_records(self):
        public_firmware = self.create_firmware("approved-build.zip", is_verified=True)
        self.create_firmware("pending-build.zip", is_verified=False)

        response = self.client.get(reverse("firmwares:browse"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, public_firmware.original_file_name)
        self.assertNotContains(response, "pending-build.zip")

    def test_model_detail_links_to_model_and_brand_articles(self):
        response = self.client.get(
            reverse(
                "firmwares:model_detail",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Device guides and articles")
        self.assertContains(
            response,
            reverse(
                "blog:model_posts",
                kwargs={
                    "brand_slug": self.brand.slug,
                    "model_slug": self.model.slug,
                },
            ),
        )
        self.assertContains(
            response,
            reverse("blog:brand_posts", kwargs={"brand_slug": self.brand.slug}),
        )

    def test_firmware_detail_shows_only_live_related_guides(self):
        author = get_user_model().objects.create_user(
            username="firmware-guide-author",
            email="firmware-guide-author@example.com",
            password="firmware-author-test-password",
        )
        firmware = self.create_firmware("guide-linked-build.zip", is_verified=True)
        live_post = Post.objects.create(
            title="Live device setup guide",
            summary="Steps for the supported device.",
            body="<p>Read the verified device setup instructions.</p>",
            author=author,
            firmware_brand=self.brand,
            firmware_model=self.model,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now(),
        )
        future_post = Post.objects.create(
            title="Future device guide",
            body="<p>This guide is not public yet.</p>",
            author=author,
            firmware_brand=self.brand,
            firmware_model=self.model,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(
            reverse(
                "firmwares:firmware_detail",
                kwargs={
                    "firmware_type": "official",
                    "firmware_id": str(firmware.pk),
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guides for this device")
        self.assertContains(response, live_post.title)
        self.assertNotContains(response, future_post.title)

    def test_external_firmware_has_a_report_form_and_badge(self):
        external = self.create_external_firmware()
        local = self.create_firmware("local-build.zip", is_verified=True)

        external_page = self.client.get(
            reverse(
                "firmwares:firmware_detail",
                kwargs={"firmware_type": "official", "firmware_id": external.pk},
            )
        )
        local_page = self.client.get(
            reverse(
                "firmwares:firmware_detail",
                kwargs={"firmware_type": "official", "firmware_id": local.pk},
            )
        )

        self.assertContains(external_page, "External download")
        self.assertContains(external_page, "Report a broken download link")
        self.assertContains(
            external_page,
            f'action="{reverse("firmwares:firmware_link_report", kwargs={"firmware_type": "official", "firmware_id": external.pk})}"',
            html=False,
        )
        self.assertNotContains(local_page, "Report a broken download link")

    def test_external_link_report_is_csrf_protected_bounded_and_admin_visible(self):
        external = self.create_external_firmware("reported-build.zip")
        client = Client(enforce_csrf_checks=True)
        detail_url = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": external.pk},
        )
        report_url = reverse(
            "firmwares:firmware_link_report",
            kwargs={"firmware_type": "official", "firmware_id": external.pk},
        )
        client.get(detail_url)
        csrf = client.cookies["csrftoken"].value

        rejected = client.post(report_url, {"reason": "broken"})
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(FirmwareLinkReport.objects.exists())

        submitted = client.post(
            report_url,
            {
                "reason": FirmwareLinkReport.Reason.REMOVED,
                "details": "The host returns a not found page.",
                "csrfmiddlewaretoken": csrf,
            },
        )
        self.assertEqual(submitted.status_code, 302)
        report = FirmwareLinkReport.objects.get()
        self.assertEqual(report.firmware, external)
        self.assertEqual(report.firmware_name, "reported-build.zip")
        self.assertIsNone(report.reporter)

        superuser = get_user_model().objects.create_superuser(
            username="firmware-link-report-admin",
            email="firmware-link-report-admin@example.com",
            password="firmware-link-admin-test-password",
        )
        client.force_login(superuser)
        admin_page = client.get(
            reverse("admin:firmwares_firmwarelinkreport_changelist")
        )
        self.assertEqual(admin_page.status_code, 200)
        self.assertContains(admin_page, "reported-build.zip")
        details_page = client.get(
            reverse(
                "admin:firmwares_firmwarelinkreport_change",
                args=[report.pk],
            )
        )
        self.assertEqual(details_page.status_code, 200)
        self.assertContains(details_page, "The host returns a not found page.")

    def test_internal_firmware_cannot_receive_an_external_link_report(self):
        local = self.create_firmware("local-build.zip", is_verified=True)
        response = self.client.post(
            reverse(
                "firmwares:firmware_link_report",
                kwargs={"firmware_type": "official", "firmware_id": local.pk},
            ),
            {"reason": "broken"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(FirmwareLinkReport.objects.exists())

    def test_external_link_report_rejects_invalid_reason_and_oversized_details(self):
        external = self.create_external_firmware("bounded-report.zip")
        report_url = reverse(
            "firmwares:firmware_link_report",
            kwargs={"firmware_type": "official", "firmware_id": external.pk},
        )

        invalid_reason = self.client.post(
            report_url, {"reason": "not-a-supported-reason"}
        )
        oversized_details = self.client.post(
            report_url,
            {
                "reason": FirmwareLinkReport.Reason.BROKEN,
                "details": "x" * 1001,
            },
        )

        self.assertEqual(invalid_reason.status_code, 302)
        self.assertEqual(oversized_details.status_code, 302)
        self.assertFalse(FirmwareLinkReport.objects.exists())

    def test_a_reporter_cannot_create_duplicate_open_reports(self):
        external = self.create_external_firmware("duplicate-report.zip")
        reporter = get_user_model().objects.create_user(
            username="link-report-member",
            email="link-report-member@example.com",
            password="link-report-test-password",
        )
        self.client.force_login(reporter)
        report_url = reverse(
            "firmwares:firmware_link_report",
            kwargs={"firmware_type": "official", "firmware_id": external.pk},
        )
        data = {"reason": FirmwareLinkReport.Reason.BROKEN}

        first = self.client.post(report_url, data)
        second = self.client.post(report_url, data)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            FirmwareLinkReport.objects.filter(
                reporter=reporter, status=FirmwareLinkReport.Status.OPEN
            ).count(),
            1,
        )

    def test_external_link_report_keeps_its_file_snapshot_after_record_deletion(self):
        external = self.create_external_firmware("removed-record.zip")
        report = FirmwareLinkReport.objects.create(
            **ref_kwargs(external),
            firmware_name=external.original_file_name,
            reason=FirmwareLinkReport.Reason.REMOVED,
        )

        external.delete()

        report.refresh_from_db()
        self.assertIsNone(report.firmware)
        self.assertEqual(report.firmware_name, "removed-record.zip")

    def test_unreviewed_firmware_cannot_be_opened_or_downloaded(self):
        firmware = self.create_firmware("pending-build.zip", is_verified=False)
        params = {
            "firmware_type": "official",
            "firmware_id": str(firmware.pk),
        }

        detail = self.client.get(reverse("firmwares:firmware_detail", kwargs=params))
        download = self.client.get(
            reverse("firmwares:firmware_download", kwargs=params)
        )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(download.status_code, 404)

    def test_htmx_firmware_filter_uses_fragment_template(self):
        response = self.client.get(
            reverse("firmwares:browse"),
            {"brand": self.brand.slug, "q": "approved"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<!doctype html>")

    def test_unclassified_firmware_is_publicly_routable_when_verified(self):
        firmware = UnclassifiedFirmware.objects.create(
            original_file_name="reviewed-unclassified.zip",
            stored_file_path="/firmware/reviewed-unclassified.zip",
            brand=self.brand,
            model=self.model,
            is_verified=True,
        )

        response = self.client.get(
            reverse("firmwares:browse"),
            {"type": "unclassified"},
        )
        detail = self.client.get(
            reverse(
                "firmwares:firmware_detail",
                kwargs={
                    "firmware_type": "unclassified",
                    "firmware_id": str(firmware.pk),
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "reviewed-unclassified.zip")
        self.assertContains(response, "Unclassified")
        self.assertEqual(detail.status_code, 200)

    def test_brand_and_model_counts_include_only_public_unclassified_firmware(self):
        UnclassifiedFirmware.objects.create(
            original_file_name="reviewed-unclassified.zip",
            stored_file_path="/firmware/reviewed-unclassified.zip",
            brand=self.brand,
            model=self.model,
            is_verified=True,
        )
        UnclassifiedFirmware.objects.create(
            original_file_name="pending-unclassified.zip",
            stored_file_path="/firmware/pending-unclassified.zip",
            brand=self.brand,
            model=self.model,
            is_verified=False,
        )

        brand_list = self.client.get(reverse("firmwares:brand_list"))
        brand_detail = self.client.get(
            reverse("firmwares:brand_detail", kwargs={"slug": self.brand.slug})
        )

        self.assertEqual(brand_list.status_code, 200)
        self.assertEqual(brand_list.context["brands"][0].firmware_count, 1)
        self.assertEqual(brand_detail.status_code, 200)
        self.assertEqual(brand_detail.context["models"][0].firmware_count, 1)

    def test_package_format_filter_is_distinct_from_review_classification(self):
        fastboot = self.create_firmware(
            "xiaomi-fastboot.tgz",
            is_verified=True,
        )
        fastboot.package_format = FirmwarePackageFormat.FASTBOOT_ROM
        fastboot.save(update_fields=["package_format"])
        recovery = self.create_firmware(
            "xiaomi-recovery.zip",
            is_verified=True,
        )
        recovery.package_format = FirmwarePackageFormat.RECOVERY_ROM
        recovery.save(update_fields=["package_format"])

        response = self.client.get(
            reverse("firmwares:browse"),
            {"type": "official", "package_format": "fastboot_rom"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, fastboot.original_file_name)
        self.assertNotContains(response, recovery.original_file_name)
        self.assertContains(response, "Fastboot ROM")
        self.assertContains(response, "Firmware classification")

    def test_flashing_tool_detail_links_to_its_official_url(self):
        category = FlashingToolCategory.objects.create(
            name="Official utilities",
            slug="official-utilities",
        )
        tool = FlashingTool.objects.create(
            category=category,
            name="Vendor Utility",
            slug="vendor-utility",
            official_url="https://tools.example.test/vendor",
        )

        response = self.client.get(
            reverse("firmwares:flashing_tool_detail", kwargs={"slug": tool.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, tool.official_url)

    def test_staff_can_add_flashing_tool_category_from_admin(self):
        admin_user = get_user_model().objects.create_user(
            username="tool-category-admin",
            email="tool-category-admin@example.test",
            password="test-password-for-admin",
            is_staff=True,
            is_superuser=True,
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(admin_user)
        add_url = reverse("admin:firmwares_flashingtoolcategory_add")

        page = client.get(add_url)
        self.assertEqual(page.status_code, 200)
        csrf_token = client.cookies["csrftoken"].value
        response = client.post(
            add_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "name": "Community Utilities",
                "slug": "community-utilities",
                "description": "Tools maintained by the community.",
                "icon": "wrench",
                "sort_order": "3",
                "is_active": "on",
                "_save": "Save",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            FlashingToolCategory.objects.filter(
                slug="community-utilities",
                name="Community Utilities",
            ).exists()
        )
