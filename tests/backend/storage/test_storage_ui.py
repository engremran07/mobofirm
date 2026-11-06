from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.storage import services
from apps.storage.models import FirmwareStorageLocation

PASSWORD = "Pass-12345!"


class StorageUiTestCase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        override = override_settings(FIRMWARE_STORAGE_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            username="ui_storage_staff",
            email="ui-storage-staff@example.com",
            password=PASSWORD,
            is_staff=True,
        )
        self.member = user_model.objects.create_user(
            username="ui_storage_member",
            email="ui-storage-member@example.com",
            password=PASSWORD,
        )
        brand = Brand.objects.create(name="Store Brand", slug="store-brand")
        self.model = Model.objects.create(brand=brand, name="S1", slug="s1")
        self.brand = brand

    def firmware(
        self, name: str, *, stage: bytes | None = b"payload"
    ) -> OfficialFirmware:
        path = self.root / "official" / name
        if stage is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(stage)
        firmware = OfficialFirmware.objects.create(
            original_file_name=name,
            stored_file_path=str(path),
            source_type="stored",
            brand=self.brand,
            model=self.model,
            is_verified=True,
            is_active=True,
        )
        # Saving syncs only after commit, which TestCase never reaches.
        services.sync_firmware_location(firmware)
        return firmware

    def staff_client(self) -> Client:
        client = Client()
        client.force_login(self.staff)
        return client


class OverviewTests(StorageUiTestCase):
    def test_overview_counts_health_and_capacity(self):
        good = self.firmware("good.zip")
        self.firmware("lost.zip", stage=None)
        for location in FirmwareStorageLocation.objects.all():
            services.verify_location(location)
        self.assertEqual(
            FirmwareStorageLocation.objects.get(object_id=str(good.pk)).status,
            FirmwareStorageLocation.Status.AVAILABLE,
        )

        overview = services.status_overview()

        self.assertEqual(overview.total, 2)
        self.assertEqual(overview.healthy_percent, 50)
        self.assertEqual(overview.healthy_text, "50%")
        self.assertEqual(overview.managed_bytes, len(b"payload"))
        disk = overview.disk
        if disk is None:
            self.fail("The overview should report disk usage.")
        self.assertLessEqual(disk.percent_used, 100)

    def test_overview_without_locations_is_healthy_and_handles_a_missing_root(self):
        with override_settings(FIRMWARE_STORAGE_ROOT=self.root / "missing"):
            overview = services.status_overview()

        self.assertEqual((overview.total, overview.healthy_percent), (0, 100))
        self.assertIsNone(overview.disk)

    def test_page_shows_capacity_providers_and_disabled_cloud_states(self):
        self.firmware("good.zip")

        response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, "Capacity and health")
        self.assertContains(response, "Disk free")
        self.assertContains(response, "<meter")
        for provider in ("Local filesystem", "Google Drive", "Amazon S3"):
            self.assertContains(response, provider)
        self.assertContains(
            response, "Disabled: STORAGE_GOOGLE_DRIVE_ENABLED is false."
        )
        self.assertContains(response, "Disabled: STORAGE_S3_ENABLED is false.")
        self.assertContains(response, "Local filesystem storage is the default")

    def test_page_handles_a_missing_storage_root(self):
        with override_settings(FIRMWARE_STORAGE_ROOT=self.root / "nowhere"):
            response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, "the storage root does not exist yet")

    def test_empty_state_explains_how_to_backfill(self):
        response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, "No locations match")
        self.assertContains(response, "sync_storage_locations")


class LocationListTests(StorageUiTestCase):
    def setUp(self):
        super().setUp()
        self.good = self.firmware("good.zip")
        self.lost = self.firmware("lost.zip", stage=None)
        for location in FirmwareStorageLocation.objects.all():
            services.verify_location(location)

    def test_rows_link_to_the_public_page_and_the_admin_form(self):
        response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, self.model.display_name)
        self.assertContains(response, "good.zip")
        self.assertContains(
            response,
            reverse(
                "firmwares:firmware_detail",
                kwargs={"firmware_type": "official", "firmware_id": str(self.good.pk)},
            ),
        )
        self.assertContains(
            response,
            reverse("admin:firmwares_officialfirmware_change", args=[self.good.pk]),
        )
        self.assertContains(response, "File not found on disk.")

    def test_filters_narrow_by_status_provider_and_firmware_id(self):
        client = self.staff_client()
        url = reverse("storage:status")

        missing = client.get(url, {"status": "missing"})
        by_id = client.get(url, {"q": str(self.good.pk)})
        external = client.get(url, {"provider": "external_link"})

        self.assertEqual(len(missing.context["rows"]), 1)
        self.assertEqual(len(by_id.context["rows"]), 1)
        self.assertEqual(len(external.context["rows"]), 0)

    def test_unknown_filter_values_are_ignored_not_reflected(self):
        response = self.staff_client().get(
            reverse("storage:status"), {"status": "<b>x</b>", "provider": "nope"}
        )

        self.assertEqual(len(response.context["rows"]), 2)
        self.assertNotContains(response, "<b>x</b>")

    def test_a_location_whose_firmware_record_is_gone_is_still_listed(self):
        FirmwareStorageLocation.objects.filter(object_id=str(self.lost.pk)).update(
            object_id=str(uuid.uuid4())
        )

        response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, "record missing")

    def test_pagination_is_applied(self):
        with mock.patch("apps.storage.views.PAGE_SIZE", 1):
            response = self.staff_client().get(reverse("storage:status"))

        self.assertContains(response, "Page 1 of 2")


class VerifyPendingTests(StorageUiTestCase):
    def test_button_verifies_pending_locations_in_a_bounded_batch(self):
        self.firmware("a.zip")
        self.firmware("b.zip")
        client = self.staff_client()

        page = client.get(reverse("storage:status"))
        self.assertContains(page, "Verify 2 pending")
        response = client.post(reverse("storage:verify_pending"))

        self.assertRedirects(response, reverse("storage:status"))
        self.assertFalse(
            FirmwareStorageLocation.objects.filter(
                status=FirmwareStorageLocation.Status.PENDING
            ).exists()
        )

    def test_verify_pending_requires_staff_post_and_csrf(self):
        url = reverse("storage:verify_pending")
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        member = Client()
        member.force_login(self.member)

        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(member.post(url).status_code, 302)
        self.assertEqual(csrf.post(url).status_code, 403)
        self.assertEqual(self.staff_client().get(url).status_code, 405)

    def test_batch_size_is_capped(self):
        for index in range(3):
            self.firmware(f"{index}.zip")

        with mock.patch("apps.storage.views.VERIFY_BATCH", 2):
            self.staff_client().post(reverse("storage:verify_pending"))

        pending = FirmwareStorageLocation.objects.filter(
            status=FirmwareStorageLocation.Status.PENDING
        ).count()
        self.assertEqual(pending, 1)


class FirmwarePageSeamTests(StorageUiTestCase):
    def detail_url(self, firmware) -> str:
        return reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": "official", "firmware_id": str(firmware.pk)},
        )

    def test_staff_see_storage_locations_on_the_firmware_page(self):
        firmware = self.firmware("seam.zip")
        services.verify_location(FirmwareStorageLocation.objects.get())

        response = self.staff_client().get(self.detail_url(firmware))

        self.assertContains(response, "Storage (staff only)")
        self.assertContains(response, "Local filesystem")
        self.assertContains(response, "Available")
        self.assertContains(response, f"{reverse('storage:status')}?q={firmware.pk}")

    def test_staff_see_when_no_location_is_recorded(self):
        firmware = self.firmware("seam.zip")
        FirmwareStorageLocation.objects.all().delete()

        response = self.staff_client().get(self.detail_url(firmware))

        self.assertContains(response, "No storage location is recorded")

    def test_members_and_visitors_never_see_storage_information(self):
        firmware = self.firmware("seam.zip")
        member = Client()
        member.force_login(self.member)

        for client in (self.client, member):
            response = client.get(self.detail_url(firmware))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Storage (staff only)")
            self.assertNotContains(response, str(self.root))
