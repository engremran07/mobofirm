from django.contrib.auth import get_user_model
from django.test import Client
from django.test import TestCase
from django.urls import reverse

from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)


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
