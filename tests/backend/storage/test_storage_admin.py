from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.storage import services
from apps.storage.models import FirmwareStorageLocation

DIALOG = {"_form_submitted": "on"}


class StorageAdminTests(TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        override = override_settings(FIRMWARE_STORAGE_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        self.admin_user = get_user_model().objects.create_superuser(
            username="storage-admin", email="storage-admin@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin_user)
        brand = Brand.objects.create(name="Admin Store", slug="admin-store")
        model = Model.objects.create(brand=brand, name="A1", slug="a1")
        path = self.root / "official" / "present.zip"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"payload")
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="present.zip",
            stored_file_path=str(path),
            source_type="stored",
            brand=brand,
            model=model,
            is_verified=True,
        )
        services.sync_firmware_location(self.firmware)
        self.location = FirmwareStorageLocation.objects.get()

    def test_verify_now_dialog_records_the_health_of_one_location(self) -> None:
        url = reverse(
            "admin:storage_firmwarestoragelocation_verify_now",
            args=[self.location.pk],
        )

        opened = self.client.get(url)
        done = self.client.post(url, DIALOG)

        self.assertEqual(opened.status_code, 200)
        self.assertIn("HX-Redirect", done.headers)
        self.location.refresh_from_db()
        self.assertEqual(self.location.status, FirmwareStorageLocation.Status.AVAILABLE)
        self.assertIsNotNone(self.location.last_verified_at)

    def test_verify_now_reports_a_missing_file(self) -> None:
        Path(self.firmware.stored_file_path).unlink()
        url = reverse(
            "admin:storage_firmwarestoragelocation_verify_now",
            args=[self.location.pk],
        )

        self.client.post(url, DIALOG)

        self.location.refresh_from_db()
        self.assertEqual(self.location.status, FirmwareStorageLocation.Status.MISSING)

    def test_bulk_verify_action_still_works(self) -> None:
        self.client.post(
            reverse("admin:storage_firmwarestoragelocation_changelist"),
            {
                "action": "verify_selected",
                "_selected_action": [str(self.location.pk)],
            },
        )

        self.location.refresh_from_db()
        self.assertEqual(self.location.status, FirmwareStorageLocation.Status.AVAILABLE)

    def test_sync_catalogue_creates_missing_location_rows(self) -> None:
        FirmwareStorageLocation.objects.all().delete()

        response = self.client.post(
            reverse("admin:storage_firmwarestoragelocation_sync_catalogue"), DIALOG
        )

        self.assertIn("HX-Redirect", response.headers)
        self.assertEqual(FirmwareStorageLocation.objects.count(), 1)

    def test_users_without_change_permission_are_refused(self) -> None:
        viewer = get_user_model().objects.create_user(
            username="storage-viewer",
            email="storage-viewer@example.test",
            is_staff=True,
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_firmwarestoragelocation")
        )
        client = Client()
        client.force_login(viewer)

        verify = client.post(
            reverse(
                "admin:storage_firmwarestoragelocation_verify_now",
                args=[self.location.pk],
            ),
            DIALOG,
        )
        sync = client.post(
            reverse("admin:storage_firmwarestoragelocation_sync_catalogue"), DIALOG
        )
        overview = client.get(
            reverse("admin:storage_firmwarestoragelocation_open_status")
        )

        self.assertEqual(verify.status_code, 403)
        self.assertEqual(sync.status_code, 403)
        self.assertEqual(overview.status_code, 302)
        self.location.refresh_from_db()
        self.assertEqual(self.location.status, FirmwareStorageLocation.Status.PENDING)

    def test_changelist_and_forms_render(self) -> None:
        for name in (
            reverse("admin:storage_firmwarestoragelocation_changelist"),
            reverse("admin:storage_firmwarestoragelocation_add"),
            reverse(
                "admin:storage_firmwarestoragelocation_change", args=[self.location.pk]
            ),
        ):
            with self.subTest(url=name):
                self.assertEqual(self.client.get(name).status_code, 200)
