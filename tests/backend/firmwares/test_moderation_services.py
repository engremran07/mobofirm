from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.firmwares.models import (
    Brand,
    DownloadToken,
    EngineeringFirmware,
    FirmwarePackageFormat,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
    PendingFirmware,
    UnclassifiedFirmware,
    Variant,
)
from apps.firmwares.services import (
    approve_pending_firmware,
    handle_upload,
    moderate_and_route,
    reject_pending_firmware,
    routed_record,
    submit_external_firmware,
    withdraw_firmware,
)
from apps.users.models import Notification

EXTERNAL_URL = "https://downloads.example.invalid/phone/update.zip"


class ModerationTestCase(TestCase):
    def setUp(self):
        self._storage = tempfile.TemporaryDirectory()
        self.addCleanup(self._storage.cleanup)
        override = override_settings(FIRMWARE_STORAGE_ROOT=Path(self._storage.name))
        override.enable()
        self.addCleanup(override.disable)

        user_model = get_user_model()
        self.uploader = user_model.objects.create_user(
            username="fw-uploader", email="fw-uploader@example.test"
        )
        self.reviewer = user_model.objects.create_superuser(
            username="fw-reviewer", email="fw-reviewer@example.test"
        )
        self.brand = Brand.objects.create(name="Moderation Brand", slug="mod-brand")
        self.model = Model.objects.create(
            brand=self.brand, name="Mod Phone", slug="mod-phone"
        )
        self.variant = Variant.objects.create(
            model=self.model, name="Global", slug="global"
        )

    def upload(self, name="update.zip", content=b"firmware-bytes", **extra):
        values: dict[str, Any] = {
            "uploader": self.uploader,
            "uploaded_brand": self.brand,
            "uploaded_model": self.model,
            "uploaded_variant": self.variant,
            "file_obj": SimpleUploadedFile(name, content),
            "is_password_protected": False,
            "password": None,
        }
        values.update(extra)
        return handle_upload(**values)


class ApprovalTests(ModerationTestCase):
    def test_approval_routes_the_file_and_publishes_a_verified_record(self):
        pending = self.upload(package_format=FirmwarePackageFormat.FASTBOOT_ROM)
        staged = Path(pending.stored_file_path)

        with self.captureOnCommitCallbacks(execute=True):
            record = approve_pending_firmware(
                pending,
                self.reviewer,
                category="official",
                notes="Checked against the vendor checksum.",
            )

        pending.refresh_from_db()
        self.assertIsInstance(record, OfficialFirmware)
        self.assertTrue(record.is_verified)
        self.assertEqual(record.reviewed_by, self.reviewer)
        self.assertIsNotNone(record.reviewed_at)
        self.assertEqual(record.package_format, FirmwarePackageFormat.FASTBOOT_ROM)
        self.assertEqual(record.file_size, len(b"firmware-bytes"))
        self.assertEqual(len(record.file_hash), 64)
        final_path = Path(record.stored_file_path)
        self.assertTrue(final_path.is_file())
        self.assertFalse(staged.exists())
        self.assertTrue(
            final_path.resolve().is_relative_to(Path(self._storage.name).resolve())
        )
        self.assertEqual(pending.admin_decision, "approved")
        self.assertEqual(pending.reviewed_by, self.reviewer)
        self.assertEqual(pending.routed_object_id, record.pk)
        self.assertEqual(routed_record(pending), record)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.uploader, action_type="moderation"
            ).exists()
        )

    def test_approval_is_idempotent(self):
        pending = self.upload()

        first = approve_pending_firmware(pending, self.reviewer, category="official")
        second = approve_pending_firmware(pending, self.reviewer, category="official")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OfficialFirmware.objects.count(), 1)

    def test_missing_category_routes_to_unclassified_not_the_ai_guess(self):
        pending = self.upload()
        PendingFirmware.objects.filter(pk=pending.pk).update(ai_category="official")
        pending.refresh_from_db()

        record = approve_pending_firmware(pending, self.reviewer)

        self.assertIsInstance(record, UnclassifiedFirmware)
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_subtype_is_kept_for_categories_that_have_one(self):
        pending = self.upload()

        record = approve_pending_firmware(
            pending, self.reviewer, category="engineering", subtype="factory"
        )

        self.assertIsInstance(record, EngineeringFirmware)
        self.assertEqual(record.subtype, "factory")

    def test_only_staff_with_the_review_permission_may_decide(self):
        pending = self.upload()
        plain_staff = get_user_model().objects.create_user(
            username="plain-staff", email="plain-staff@example.test", is_staff=True
        )

        for actor in (self.uploader, plain_staff):
            with self.assertRaises(PermissionDenied):
                approve_pending_firmware(pending, actor, category="official")
            with self.assertRaises(PermissionDenied):
                reject_pending_firmware(pending, actor, notes="no")

        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "pending")
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_invalid_category_and_hierarchy_are_rejected_without_side_effects(self):
        pending = self.upload()
        other_brand = Brand.objects.create(name="Other Brand", slug="other-brand")
        staged = Path(pending.stored_file_path)

        with self.assertRaises(ValidationError):
            approve_pending_firmware(pending, self.reviewer, category="bogus")
        with self.assertRaises(ValidationError):
            approve_pending_firmware(
                pending, self.reviewer, category="official", brand=other_brand
            )
        with self.assertRaises(ValidationError):
            approve_pending_firmware(
                pending,
                self.reviewer,
                category="official",
                package_format="not-a-format",
            )

        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "pending")
        self.assertTrue(staged.is_file())

    def test_duplicate_file_and_build_returns_the_file_to_staging(self):
        first = self.upload("dup.zip")
        approve_pending_firmware(first, self.reviewer, category="official")
        second = self.upload("dup.zip", b"other-bytes")
        staged = Path(second.stored_file_path)

        with self.assertRaises(ValidationError):
            approve_pending_firmware(second, self.reviewer, category="official")

        second.refresh_from_db()
        self.assertEqual(second.admin_decision, "pending")
        self.assertTrue(staged.is_file())
        self.assertEqual(OfficialFirmware.objects.count(), 1)

    def test_moderate_and_route_dispatches_both_decisions(self):
        approved = self.upload("one.zip")
        rejected = self.upload("two.zip")

        record = moderate_and_route(
            approved, "approved", admin_user=self.reviewer, category="official"
        )
        moderate_and_route(
            rejected, "rejected", admin_user=self.reviewer, notes="Corrupt archive"
        )

        self.assertTrue(record.is_verified)
        rejected.refresh_from_db()
        self.assertEqual(rejected.admin_decision, "rejected")
        with self.assertRaises(ValidationError):
            moderate_and_route(approved, "maybe", admin_user=self.reviewer)


class RejectionAndWithdrawalTests(ModerationTestCase):
    def test_rejection_needs_a_reason_and_removes_the_staged_file(self):
        pending = self.upload()
        staged = Path(pending.stored_file_path)

        with self.assertRaises(ValidationError):
            reject_pending_firmware(pending, self.reviewer, notes="  ")

        with self.captureOnCommitCallbacks(execute=True):
            reject_pending_firmware(pending, self.reviewer, notes="Wrong device.")

        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "rejected")
        self.assertEqual(pending.admin_notes, "Wrong device.")
        self.assertEqual(pending.reviewed_by, self.reviewer)
        self.assertFalse(staged.exists())
        notification = Notification.objects.get(
            recipient=self.uploader, action_type="moderation"
        )
        self.assertIn("Wrong device.", notification.message)
        with self.assertRaises(ValidationError):
            approve_pending_firmware(pending, self.reviewer, category="official")

    def test_rejection_is_idempotent(self):
        pending = self.upload()

        first = reject_pending_firmware(pending, self.reviewer, notes="Bad")
        second = reject_pending_firmware(pending, self.reviewer, notes="Bad")

        self.assertEqual(first.pk, second.pk)

    def test_withdrawal_returns_an_approved_upload_to_pending_for_resubmission(self):
        pending = self.upload()
        record = approve_pending_firmware(pending, self.reviewer, category="official")
        DownloadToken.objects.create(
            firmware=record,
            token="withdraw-token",
            expires_at=record.created_at,
        )
        final_path = Path(record.stored_file_path)

        with self.assertRaises(ValidationError):
            withdraw_firmware(pending, self.reviewer, reason=" ")
        withdraw_firmware(pending, self.reviewer, reason="Wrong build uploaded.")

        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "pending")
        self.assertFalse(pending.category_locked)
        self.assertIsNone(pending.routed_object_id)
        self.assertFalse(OfficialFirmware.objects.exists())
        self.assertFalse(DownloadToken.objects.exists())
        self.assertFalse(final_path.exists())
        self.assertTrue(Path(pending.stored_file_path).is_file())

        again = approve_pending_firmware(pending, self.reviewer, category="engineering")
        self.assertIsInstance(again, EngineeringFirmware)
        self.assertTrue(Path(again.stored_file_path).is_file())

    def test_only_approved_uploads_can_be_withdrawn(self):
        pending = self.upload()

        with self.assertRaises(ValidationError):
            withdraw_firmware(pending, self.reviewer, reason="Nothing to withdraw")


class ExternalFirmwareTests(ModerationTestCase):
    def submit(self, **extra):
        values: dict[str, Any] = {
            "uploader": self.uploader,
            "url": EXTERNAL_URL,
            "original_file_name": "update.zip",
            "uploaded_brand": self.brand,
            "uploaded_model": self.model,
        }
        values.update(extra)
        return submit_external_firmware(**values)

    def test_external_submission_is_pending_without_a_stored_file(self):
        pending = self.submit(file_hash="a" * 64, file_size=1024)

        self.assertEqual(pending.source_type, FirmwareSourceType.EXTERNAL)
        self.assertEqual(pending.external_url, EXTERNAL_URL)
        self.assertEqual(pending.stored_file_path, "")
        self.assertEqual(pending.admin_decision, "pending")
        self.assertEqual(pending.file_hash, "a" * 64)

    def test_resubmitting_a_pending_url_returns_the_same_record(self):
        first = self.submit()
        second = self.submit()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PendingFirmware.objects.count(), 1)

    def test_url_must_be_https_on_an_allowlisted_host_without_credentials(self):
        bad_urls = (
            "http://downloads.example.invalid/update.zip",
            "https://evil.example.test/update.zip",
            "https://user:pw@downloads.example.invalid/update.zip",
            "ftp://downloads.example.invalid/update.zip",
            "https:///update.zip",
            "",
        )
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                self.submit(url=url)
        self.assertFalse(PendingFirmware.objects.exists())

    @override_settings(FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS=["*.cdn.example.test"])
    def test_wildcard_host_entries_match_subdomains_only(self):
        self.submit(url="https://files.cdn.example.test/update.zip")
        with self.assertRaises(ValidationError):
            self.submit(url="https://cdn.example.test/update.zip")

    def test_file_name_and_hash_are_validated(self):
        with self.assertRaises(ValueError):
            self.submit(original_file_name="payload.exe")
        with self.assertRaises(ValueError):
            self.submit(file_hash="not-a-hash")
        with self.assertRaises(ValueError):
            self.submit(file_size=-1)

    def test_approval_creates_an_external_record_without_moving_any_file(self):
        pending = self.submit()

        record = approve_pending_firmware(pending, self.reviewer, category="official")

        self.assertEqual(record.source_type, FirmwareSourceType.EXTERNAL)
        self.assertEqual(record.external_url, EXTERNAL_URL)
        self.assertEqual(record.stored_file_path, "")
        self.assertTrue(record.is_verified)
        self.assertTrue(record.is_external)

    def test_database_requires_exactly_one_of_path_or_url(self):
        base = {"original_file_name": "constraint.zip", "brand": self.brand}
        invalid = (
            {"stored_file_path": "", "external_url": ""},
            {
                "source_type": FirmwareSourceType.STORED,
                "stored_file_path": "/f/a.zip",
                "external_url": EXTERNAL_URL,
            },
            {
                "source_type": FirmwareSourceType.EXTERNAL,
                "stored_file_path": "/f/a.zip",
                "external_url": EXTERNAL_URL,
            },
            {"source_type": FirmwareSourceType.EXTERNAL, "stored_file_path": ""},
        )
        for values in invalid:
            with (
                self.subTest(values=values),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                OfficialFirmware.objects.create(**base, **values)

    def test_model_validation_checks_the_external_host(self):
        record = OfficialFirmware(
            original_file_name="clean.zip",
            source_type=FirmwareSourceType.EXTERNAL,
            external_url="https://evil.example.test/clean.zip",
        )

        with self.assertRaises(ValidationError):
            record.full_clean()
