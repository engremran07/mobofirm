from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.firmwares.models import FirmwarePackageFormat, PendingFirmware
from apps.firmwares.serializers import PendingFirmwareUploadSerializer
from apps.firmwares.services import handle_upload


class FirmwarePackageFormatTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="firmware-uploader",
            email="firmware-uploader@example.test",
            password="test-password",
        )

    def test_upload_serializer_accepts_known_formats_and_rejects_unknown_values(self):
        valid = PendingFirmwareUploadSerializer(
            data={
                "is_password_protected": False,
                "package_format": FirmwarePackageFormat.FASTBOOT_ROM,
                "file": SimpleUploadedFile("package.tgz", b"firmware"),
            }
        )
        invalid = PendingFirmwareUploadSerializer(
            data={
                "is_password_protected": False,
                "package_format": "unrecognized",
                "file": SimpleUploadedFile("package.tgz", b"firmware"),
            }
        )

        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(
            valid.validated_data["package_format"],
            FirmwarePackageFormat.FASTBOOT_ROM,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("package_format", invalid.errors)

    def test_upload_persists_package_format_with_pending_firmware(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            override_settings(FIRMWARE_STORAGE_ROOT=Path(temp_dir)),
        ):
            pending = handle_upload(
                uploader=self.user,
                uploaded_brand=None,
                uploaded_model=None,
                uploaded_variant=None,
                file_obj=SimpleUploadedFile("update.zip", b"firmware"),
                is_password_protected=False,
                password=None,
                package_format=FirmwarePackageFormat.FULL_OTA,
            )

            self.assertEqual(
                pending.package_format,
                FirmwarePackageFormat.FULL_OTA,
            )
            self.assertTrue(Path(pending.stored_file_path).is_file())
            self.assertTrue(
                PendingFirmware.objects.filter(
                    pk=pending.pk,
                    package_format=FirmwarePackageFormat.FULL_OTA,
                ).exists()
            )

    def test_invalid_package_format_is_rejected_before_file_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = Path(temp_dir) / "storage"
            with (
                override_settings(FIRMWARE_STORAGE_ROOT=storage_root),
                self.assertRaisesMessage(
                    ValueError, "Unsupported firmware package format"
                ),
            ):
                handle_upload(
                    uploader=self.user,
                    uploaded_brand=None,
                    uploaded_model=None,
                    uploaded_variant=None,
                    file_obj=SimpleUploadedFile("update.zip", b"firmware"),
                    is_password_protected=False,
                    password=None,
                    package_format="unrecognized",
                )

            self.assertFalse(storage_root.exists())
