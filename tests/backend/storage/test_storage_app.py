from __future__ import annotations

import hashlib
import tempfile
from io import StringIO
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import OfficialFirmware
from apps.storage import providers
from apps.storage.models import FirmwareStorageLocation
from apps.storage.services import (
    delete_locations,
    sync_firmware_location,
    verify_location,
)
from apps.storage.tasks import sync_firmware_locations, verify_storage_locations

ALLOWED_URL = "https://downloads.example.invalid/fw/sample.zip"


class StorageTestCase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        override = override_settings(FIRMWARE_STORAGE_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        self.uploader = get_user_model().objects.create_user(
            username="storage_uploader",
            email="storage-uploader@example.com",
            password="Pass-12345!",
        )

    def synced(self, firmware: OfficialFirmware) -> FirmwareStorageLocation:
        location = sync_firmware_location(firmware)
        if location is None:
            raise AssertionError("The firmware was expected to be managed.")
        return location

    def firmware(self, path: str = "", *, url: str = "", **extra) -> OfficialFirmware:
        if url:
            source = {
                "source_type": "external",
                "external_url": url,
                "stored_file_path": "",
            }
            name = Path(url).name
        else:
            source = {"source_type": "stored", "stored_file_path": path}
            name = Path(path).name
        return OfficialFirmware.objects.create(
            original_file_name=name or "x.zip",
            uploader=self.uploader,
            **source,
            **extra,
        )

    def stage(self, relative: str, payload: bytes = b"firmware-bytes") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path


class ProviderTests(StorageTestCase):
    def test_local_is_the_only_usable_internal_provider_and_cloud_is_visibly_disabled(
        self,
    ):
        statuses = {status.kind: status for status in providers.provider_statuses()}

        self.assertTrue(statuses[providers.LOCAL].usable)
        for kind in (providers.GOOGLE_DRIVE, providers.S3):
            with self.subTest(kind=kind):
                self.assertFalse(statuses[kind].usable)
                self.assertTrue(statuses[kind].detail.startswith("Disabled"))

    @override_settings(STORAGE_GOOGLE_DRIVE_ENABLED=True)
    def test_enabling_the_flag_without_packages_reports_missing_packages(self):
        status = next(
            s for s in providers.provider_statuses() if s.kind == providers.GOOGLE_DRIVE
        )

        self.assertFalse(status.usable)
        self.assertIn("not installed", status.detail)

    def test_local_paths_cannot_escape_the_storage_root(self):
        for bad in ("../outside.zip", "a/../../outside.zip", ""):
            with self.subTest(path=bad), self.assertRaises(providers.StoragePathError):
                providers.resolve_local_path(bad)

        self.assertEqual(
            providers.resolve_local_path("a/b.zip"), self.root / "a" / "b.zip"
        )

    def test_external_urls_must_be_https_on_an_allow_listed_host(self):
        self.assertEqual(providers.validate_external_url(ALLOWED_URL), ALLOWED_URL)
        for bad in (
            "http://downloads.example.invalid/x.zip",
            "https://evil.invalid/x.zip",
            "https://downloads.example.invalid.evil.invalid/x.zip",
            "ftp://downloads.example.invalid/x.zip",
        ):
            with self.subTest(url=bad), self.assertRaises(providers.StorageUrlError):
                providers.validate_external_url(bad)


class LocationSyncTests(StorageTestCase):
    def test_local_firmware_gets_a_location_through_its_content_type(self):
        staged = self.stage("official/a.zip")
        firmware = self.firmware(str(staged))

        location = self.synced(firmware)

        self.assertEqual(location.provider, providers.LOCAL)
        self.assertEqual(location.relative_path, "official/a.zip")
        self.assertEqual(location.firmware, firmware)
        self.assertEqual(
            location.content_type,
            ContentType.objects.get_for_model(
                OfficialFirmware, for_concrete_model=False
            ),
        )

    def test_sync_is_idempotent_and_resets_state_only_when_the_target_changes(self):
        staged = self.stage("official/a.zip")
        firmware = self.firmware(str(staged))
        location = self.synced(firmware)
        verify_location(location)

        again = self.synced(firmware)
        self.assertEqual(again.pk, location.pk)
        again.refresh_from_db()
        self.assertEqual(again.status, FirmwareStorageLocation.Status.AVAILABLE)

        firmware.stored_file_path = str(self.stage("official/b.zip"))
        firmware.save()
        moved = self.synced(firmware)
        moved.refresh_from_db()

        self.assertEqual(moved.pk, location.pk)
        self.assertEqual(moved.relative_path, "official/b.zip")
        self.assertEqual(moved.status, FirmwareStorageLocation.Status.PENDING)
        self.assertEqual(FirmwareStorageLocation.objects.count(), 1)

    def test_unmanageable_sources_do_not_create_locations(self):
        outside = self.firmware("/definitely/not/in/root/x.zip")
        blocked_host = self.firmware(url="https://evil.invalid/x.zip")

        for firmware in (outside, blocked_host):
            with self.subTest(
                source=firmware.stored_file_path or firmware.external_url
            ):
                self.assertIsNone(sync_firmware_location(firmware))
        self.assertFalse(FirmwareStorageLocation.objects.exists())

    def test_allow_listed_dummy_link_becomes_an_external_location(self):
        location = self.synced(self.firmware(url=ALLOWED_URL))

        self.assertEqual(location.provider, providers.EXTERNAL_LINK)
        self.assertEqual(location.external_url, ALLOWED_URL)
        self.assertEqual(
            verify_location(location).status, FirmwareStorageLocation.Status.AVAILABLE
        )

    def test_saving_firmware_syncs_after_commit_and_deleting_removes_locations(self):
        staged = self.stage("official/signal.zip")
        with self.captureOnCommitCallbacks(execute=True):
            firmware = self.firmware(str(staged))

        self.assertEqual(FirmwareStorageLocation.objects.count(), 1)

        firmware.delete()

        self.assertFalse(FirmwareStorageLocation.objects.exists())

    def test_delete_locations_returns_the_removed_count(self):
        firmware = self.firmware(str(self.stage("official/d.zip")))
        sync_firmware_location(firmware)

        self.assertEqual(delete_locations(firmware), 1)


class VerificationTests(StorageTestCase):
    def test_available_file_records_size_and_sha256(self):
        payload = b"firmware-bytes"
        firmware = self.firmware(str(self.stage("official/v.zip", payload)))
        location = verify_location(self.synced(firmware))

        self.assertEqual(location.status, FirmwareStorageLocation.Status.AVAILABLE)
        self.assertEqual(location.size_bytes, len(payload))
        self.assertEqual(location.sha256, hashlib.sha256(payload).hexdigest())
        self.assertIsNotNone(location.last_verified_at)

    def test_missing_file_is_reported_not_hidden(self):
        firmware = self.firmware(str(self.root / "official" / "gone.zip"))
        location = verify_location(self.synced(firmware))

        self.assertEqual(location.status, FirmwareStorageLocation.Status.MISSING)
        self.assertEqual(location.last_error, "File not found on disk.")

    def test_checksum_mismatch_against_the_firmware_record_is_flagged(self):
        firmware = self.firmware(str(self.stage("official/m.zip")), file_hash="0" * 64)
        location = verify_location(self.synced(firmware))

        self.assertEqual(location.status, FirmwareStorageLocation.Status.MISMATCH)

    def test_tampered_relative_path_is_never_opened(self):
        firmware = self.firmware(str(self.stage("official/t.zip")))
        location = self.synced(firmware)
        FirmwareStorageLocation.objects.filter(pk=location.pk).update(
            relative_path="../../etc/passwd"
        )
        location.refresh_from_db()

        verified = verify_location(location)

        self.assertEqual(verified.status, FirmwareStorageLocation.Status.MISSING)
        self.assertEqual(verified.last_error, "Unsafe path.")

    def test_celery_tasks_verify_and_backfill(self):
        staged = self.stage("official/task.zip")
        self.firmware(str(staged))

        backfill = sync_firmware_locations()
        counts = verify_storage_locations()

        self.assertEqual(backfill["locations_synced"], 1)
        self.assertEqual(counts, {"available": 1})

    def test_management_command_reports_synced_locations(self):
        self.firmware(str(self.stage("official/cmd.zip")))
        out = StringIO()

        call_command("sync_storage_locations", stdout=out)

        self.assertIn("locations synced: 1", out.getvalue())


class LocationModelTests(StorageTestCase):
    def test_database_requires_a_path_for_local_and_a_url_for_external(self):
        content_type = ContentType.objects.get_for_model(OfficialFirmware)
        for provider in (providers.LOCAL, providers.EXTERNAL_LINK):
            with (
                self.subTest(provider=provider),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                FirmwareStorageLocation.objects.create(
                    content_type=content_type, object_id="1", provider=provider
                )

    def test_clean_rejects_unsafe_values_and_disabled_providers(self):
        content_type = ContentType.objects.get_for_model(OfficialFirmware)
        cases = (
            {"provider": providers.LOCAL, "relative_path": "../x"},
            {
                "provider": providers.EXTERNAL_LINK,
                "external_url": "https://evil.invalid/x",
            },
            {"provider": providers.S3},
        )
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                FirmwareStorageLocation(
                    content_type=content_type, object_id="1", **fields
                ).clean()


class StorageViewTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.staff = get_user_model().objects.create_user(
            username="storage_staff",
            email="storage-staff@example.com",
            password="Pass-12345!",
            is_staff=True,
        )
        firmware = self.firmware(str(self.root / "official" / "gone.zip"))
        self.location = self.synced(firmware)

    def test_status_page_is_staff_only(self):
        url = reverse("storage:status")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.uploader)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google Drive")
        self.assertContains(response, "Disabled")
        self.assertContains(response, "Local filesystem")

    def test_verify_requires_staff_post_and_csrf(self):
        url = reverse("storage:verify", kwargs={"location_id": self.location.pk})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post(url).status_code, 403)

        self.client.force_login(self.staff)
        response = self.client.post(url)
        self.assertRedirects(response, reverse("storage:status"))
        self.location.refresh_from_db()
        self.assertEqual(self.location.status, FirmwareStorageLocation.Status.MISSING)

    def test_admin_registration_and_verify_action(self):
        self.assertIn(FirmwareStorageLocation, admin.site._registry)
        model_admin = admin.site._registry[FirmwareStorageLocation]
        self.assertIn("verify_selected", model_admin.actions or ())
