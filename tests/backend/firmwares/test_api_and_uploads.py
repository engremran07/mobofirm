from __future__ import annotations

import io
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.firmwares.models import (
    Brand,
    BrandCreationRequest,
    EngineeringFirmware,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
    PendingFirmware,
    TrustedTester,
    VerificationReport,
)
from apps.firmwares.services import (
    FirmwareUploadError,
    attempt_extraction,
    handle_upload,
)

AI_STUB = patch(
    "apps.firmwares.ai_analysis.FirmwareAIAnalyzer._ai_based_analysis",
    return_value={"confidence": 0.0, "method": "ai"},
)


class StorageTestCase(TestCase):
    def setUp(self):
        cache.clear()
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.root = Path(storage.name)
        override = override_settings(FIRMWARE_STORAGE_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="api-user", email="api-user@example.test"
        )
        self.staff = user_model.objects.create_superuser(
            username="api-staff", email="api-staff@example.test"
        )
        self.api = APIClient()

    def upload(self, name="update.zip", content=b"firmware", **extra):
        values: dict[str, Any] = {
            "uploader": self.user,
            "uploaded_brand": None,
            "uploaded_model": None,
            "uploaded_variant": None,
            "file_obj": SimpleUploadedFile(name, content),
            "is_password_protected": False,
            "password": None,
        }
        values.update(extra)
        return handle_upload(**values)


class UploadValidationTests(StorageTestCase):
    def test_hash_and_size_are_computed_from_the_stored_bytes(self):
        pending = self.upload(content=b"abc")

        self.assertEqual(pending.file_size, 3)
        self.assertEqual(
            pending.file_hash,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(pending.source_type, FirmwareSourceType.STORED)

    def test_disallowed_extensions_content_types_and_empty_files_are_refused(self):
        for name, content_type in (
            ("payload.exe", "application/octet-stream"),
            ("page.html", "text/html"),
            ("archive.zip", "text/html"),
            ("archive.zip", "application/x-msdownload"),
        ):
            with self.subTest(name=name, content_type=content_type):
                upload = SimpleUploadedFile(name, b"data", content_type=content_type)
                with self.assertRaises(FirmwareUploadError):
                    self.upload(file_obj=upload)
        with self.assertRaises(FirmwareUploadError):
            self.upload(content=b"")
        self.assertFalse(PendingFirmware.objects.exists())
        self.assertEqual(list((self.root / "pending").glob("*")), [])

    @override_settings(FIRMWARE_MAX_UPLOAD_BYTES=10)
    def test_the_size_limit_applies_to_declared_and_actual_bytes(self):
        with self.assertRaises(FirmwareUploadError):
            self.upload(content=b"x" * 11)

        class LyingUpload(SimpleUploadedFile):
            size = 1

        with self.assertRaises(FirmwareUploadError):
            self.upload(file_obj=LyingUpload("big.zip", b"x" * 50))
        self.assertFalse(PendingFirmware.objects.exists())
        self.assertEqual(list((self.root / "pending").glob("*")), [])

    def test_path_components_in_file_names_are_stripped(self):
        pending = self.upload(name="../../evil.zip")

        self.assertEqual(pending.original_file_name, "evil.zip")
        stored = Path(pending.stored_file_path).resolve()
        self.assertTrue(stored.is_relative_to((self.root / "pending").resolve()))

    def test_hidden_file_names_are_refused(self):
        with self.assertRaises(FirmwareUploadError):
            self.upload(name=".hidden.zip")

    def test_unknown_package_format_is_a_value_error_before_any_write(self):
        with self.assertRaisesMessage(
            ValueError, "Unsupported firmware package format"
        ):
            self.upload(package_format="nope")
        self.assertFalse(self.root.joinpath("pending").exists())


class ArchiveInspectionTests(StorageTestCase):
    def build_zip(self, members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for info, data in members:
                archive.writestr(info, data)
        return buffer.getvalue()

    def protected_pending(self, payload):
        pending = self.upload(content=payload, is_password_protected=True)
        return pending

    def test_a_clean_archive_validates_without_extracting_anything(self):
        payload = self.build_zip([("boot.img", b"boot"), ("system.img", b"system")])
        pending = self.protected_pending(payload)

        attempt_extraction(pending)

        pending.refresh_from_db()
        self.assertEqual(pending.extraction_status, "success")
        self.assertEqual(pending.password_validation_status, "valid")
        self.assertEqual(
            sorted(
                path.name for path in Path(pending.stored_file_path).parent.iterdir()
            ),
            [Path(pending.stored_file_path).name],
        )

    def test_traversal_symlink_and_absolute_members_fail_validation(self):
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = {
            "traversal": [("../evil.txt", b"x")],
            "sibling-prefix": [("../" + self.root.name + "-evil/x.txt", b"x")],
            "absolute": [("/etc/passwd", b"x")],
            "symlink": [(link, b"/etc/passwd")],
        }
        for label, members in cases.items():
            with self.subTest(label=label):
                pending = self.protected_pending(self.build_zip(members))

                attempt_extraction(pending)

                pending.refresh_from_db()
                self.assertEqual(pending.extraction_status, "failed")
                self.assertEqual(pending.password_validation_status, "invalid")

    @override_settings(FIRMWARE_ZIP_MAX_MEMBERS=2)
    def test_archives_with_too_many_members_fail_validation(self):
        payload = self.build_zip([(f"part{i}.img", b"x") for i in range(3)])
        pending = self.protected_pending(payload)

        attempt_extraction(pending)

        pending.refresh_from_db()
        self.assertEqual(pending.extraction_status, "failed")

    @override_settings(FIRMWARE_ZIP_MAX_TOTAL_BYTES=10)
    def test_archives_that_expand_too_far_fail_validation(self):
        payload = self.build_zip([("big.img", b"x" * 100)])
        pending = self.protected_pending(payload)

        attempt_extraction(pending)

        pending.refresh_from_db()
        self.assertEqual(pending.extraction_status, "failed")

    def test_non_zip_protected_files_stay_pending_and_unprotected_ones_succeed(self):
        protected = self.protected_pending(b"not a zip")
        plain = self.upload(name="plain.zip", content=b"plain")

        attempt_extraction(protected)
        attempt_extraction(plain)

        protected.refresh_from_db()
        plain.refresh_from_db()
        self.assertEqual(protected.extraction_status, "pending")
        self.assertEqual(plain.extraction_status, "success")


class UploadApiTests(StorageTestCase):
    def test_upload_endpoint_persists_and_returns_a_serialisable_pending_record(self):
        self.api.force_authenticate(self.user)
        brand = Brand.objects.create(name="Upload Brand", slug="upload-brand")

        with AI_STUB:
            response = self.api.post(
                reverse("firmwares:firmware-upload"),
                {
                    "file": SimpleUploadedFile("api.zip", b"api-bytes"),
                    "is_password_protected": "false",
                    "brand_id": str(brand.pk),
                    "package_format": "full_ota",
                    "extra_info": "Build notes",
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["extra_info"], "Build notes")
        self.assertEqual(body["file_size"], len(b"api-bytes"))
        self.assertEqual(body["uploader"], self.user.pk)
        pending = PendingFirmware.objects.get(pk=body["id"])
        self.assertEqual(pending.uploaded_brand, brand)
        self.assertNotIn("stored_file_path", body)

    def test_upload_endpoint_rejects_bad_files_and_unknown_ids(self):
        self.api.force_authenticate(self.user)
        url = reverse("firmwares:firmware-upload")

        bad_type = self.api.post(
            url,
            {
                "file": SimpleUploadedFile("run.exe", b"x"),
                "is_password_protected": "false",
            },
            format="multipart",
        )
        unknown = self.api.post(
            url,
            {
                "file": SimpleUploadedFile("ok.zip", b"x"),
                "is_password_protected": "false",
                "brand_id": "9999",
            },
            format="multipart",
        )

        self.assertEqual(bad_type.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertFalse(PendingFirmware.objects.exists())

    def test_upload_requires_authentication(self):
        response = self.api.post(
            reverse("firmwares:firmware-upload"),
            {
                "file": SimpleUploadedFile("a.zip", b"x"),
                "is_password_protected": "false",
            },
            format="multipart",
        )

        self.assertIn(response.status_code, (401, 403))

    def test_browser_visit_to_the_api_is_sent_to_the_upload_page(self):
        self.api.force_authenticate(self.user)

        for name in ("firmwares:firmware-upload", "firmwares:firmware-upload-external"):
            response = self.api.get(reverse(name), HTTP_ACCEPT="text/html")

            self.assertEqual(response.status_code, 302, name)
            self.assertEqual(response["Location"], reverse("firmwares:upload"), name)

    def test_api_clients_get_json_never_the_browsable_form(self):
        self.api.force_authenticate(self.user)

        response = self.api.get(
            reverse("firmwares:firmware-upload"), HTTP_ACCEPT="application/json"
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn(b"Django REST framework", response.content)

    def test_external_submission_endpoint(self):
        self.api.force_authenticate(self.user)
        url = reverse("firmwares:firmware-upload-external")
        payload = {
            "url": "https://downloads.example.invalid/api/ext.zip",
            "file_name": "ext.zip",
            "file_size": 2048,
            "file_hash": "b" * 64,
        }

        created = self.api.post(url, payload, format="json")
        again = self.api.post(url, payload, format="json")
        bad_host = self.api.post(
            url, {**payload, "url": "https://evil.example.test/ext.zip"}, format="json"
        )
        bad_scheme = self.api.post(
            url,
            {**payload, "url": "http://downloads.example.invalid/ext.zip"},
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["source_type"], "external")
        self.assertEqual(created.json()["id"], again.json()["id"])
        self.assertEqual(bad_host.status_code, 400)
        self.assertEqual(bad_scheme.status_code, 400)
        self.assertEqual(PendingFirmware.objects.count(), 1)

    def test_pending_list_is_staff_only_and_serialises(self):
        self.upload()
        url = reverse("firmwares:pending-firmware-list")

        self.api.force_authenticate(self.user)
        denied = self.api.get(url)
        self.api.force_authenticate(self.staff)
        listing = self.api.get(url)

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["status"], "pending")


class ModerationApiTests(StorageTestCase):
    def test_staff_can_approve_reject_and_withdraw(self):
        pending = self.upload()
        url = reverse("firmwares:firmware-moderate", kwargs={"pk": pending.pk})
        self.api.force_authenticate(self.staff)

        approved = self.api.post(
            url,
            {"decision": "approved", "category": "engineering", "notes": "ok"},
            format="json",
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertTrue(
            EngineeringFirmware.objects.filter(
                pk=approved.json()["firmware_id"]
            ).exists()
        )
        withdrawn = self.api.post(
            url, {"decision": "withdrawn", "notes": "Wrong build"}, format="json"
        )
        no_reason = self.api.post(
            url, {"decision": "rejected", "notes": ""}, format="json"
        )
        rejected = self.api.post(
            url, {"decision": "rejected", "notes": "Not firmware"}, format="json"
        )
        unknown = self.api.post(url, {"decision": "maybe"}, format="json")

        self.assertEqual(withdrawn.status_code, 200)
        self.assertFalse(EngineeringFirmware.objects.exists())
        self.assertEqual(no_reason.status_code, 400)
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(unknown.status_code, 400)
        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "rejected")

    def test_non_staff_cannot_moderate(self):
        pending = self.upload()
        self.api.force_authenticate(self.user)

        response = self.api.post(
            reverse("firmwares:firmware-moderate", kwargs={"pk": pending.pk}),
            {"decision": "approved", "category": "official"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        pending.refresh_from_db()
        self.assertEqual(pending.admin_decision, "pending")


class CreationRequestApiTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.user.mark_email_verified()

    def test_unverified_accounts_cannot_submit_requests(self):
        stranger = get_user_model().objects.create_user(
            username="unverified-requester", email="unverified-requester@example.test"
        )
        self.api.force_authenticate(stranger)

        response = self.api.post(
            reverse("firmwares:brand-requests-list"), {"name": "Nope"}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BrandCreationRequest.objects.exists())

    def test_users_create_and_see_only_their_own_requests(self):
        other = get_user_model().objects.create_user(
            username="other-requester", email="other-requester@example.test"
        )
        other.mark_email_verified()
        url = reverse("firmwares:brand-requests-list")

        self.api.force_authenticate(self.user)
        created = self.api.post(
            url, {"name": "New Brand", "status": "approved"}, format="json"
        )
        mine = self.api.get(url)
        self.api.force_authenticate(other)
        theirs = self.api.get(url)
        patched = self.api.patch(
            reverse("firmwares:brand-requests-detail", args=[created.json()["id"]]),
            {"name": "Hijacked"},
            format="json",
        )

        self.assertEqual(created.status_code, 201, created.content)
        request = BrandCreationRequest.objects.get()
        self.assertEqual(request.requested_by, self.user)
        self.assertEqual(request.status, "pending")
        self.assertEqual(len(mine.json()), 1)
        self.assertEqual(theirs.json(), [])
        self.assertEqual(patched.status_code, 405)
        request.refresh_from_db()
        self.assertEqual(request.name, "New Brand")

    def test_model_and_variant_requests_do_not_error_for_regular_users(self):
        brand = Brand.objects.create(name="Req Brand", slug="req-brand")
        model = Model.objects.create(brand=brand, name="Req", slug="req")
        self.api.force_authenticate(self.user)

        models_response = self.api.post(
            reverse("firmwares:model-requests-list"),
            {"brand": brand.pk, "name": "Requested Model"},
            format="json",
        )
        variants_response = self.api.post(
            reverse("firmwares:variant-requests-list"),
            {"brand": brand.pk, "model": model.pk, "name": "Requested Variant"},
            format="json",
        )
        listing = self.api.get(reverse("firmwares:model-requests-list"))
        listing_variants = self.api.get(reverse("firmwares:variant-requests-list"))

        self.assertEqual(models_response.status_code, 201, models_response.content)
        self.assertEqual(variants_response.status_code, 201, variants_response.content)
        self.assertEqual(len(listing.json()), 1)
        self.assertEqual(len(listing_variants.json()), 1)


class VerificationReportApiTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        user_model = get_user_model()
        self.tester_user = user_model.objects.create_user(
            username="tester-a", email="tester-a@example.test"
        )
        self.tester = TrustedTester.objects.create(user=self.tester_user)
        self.other = user_model.objects.create_user(
            username="tester-b", email="tester-b@example.test"
        )
        TrustedTester.objects.create(user=self.other)
        brand = Brand.objects.create(name="Verify Brand", slug="verify-brand")
        model = Model.objects.create(brand=brand, name="Verify", slug="verify")
        self.firmware = OfficialFirmware.objects.create(
            original_file_name="verify.zip",
            stored_file_path="/f/verify.zip",
            brand=brand,
            model=model,
            is_verified=True,
        )
        self.report = VerificationReport.objects.create(
            firmware=self.firmware,
            tester=self.tester,
            verdict="pass",
            status=VerificationReport.Status.PUBLISHED,
        )
        self.url = reverse(
            "firmwares:verification-reports-detail", args=[self.report.pk]
        )

    def test_other_users_cannot_change_or_delete_someone_elses_report(self):
        self.api.force_authenticate(self.other)

        patched = self.api.patch(self.url, {"verdict": "fail"}, format="json")
        deleted = self.api.delete(self.url)

        self.assertEqual(patched.status_code, 403)
        self.assertEqual(deleted.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.verdict, "pass")

    def test_anonymous_users_can_read_published_reports_but_not_write(self):
        read = self.api.get(self.url)
        write = self.api.patch(self.url, {"verdict": "fail"}, format="json")

        self.assertEqual(read.status_code, 200)
        self.assertIn(write.status_code, (401, 403))

    def test_the_tester_can_edit_only_while_the_report_is_submitted(self):
        self.api.force_authenticate(self.tester_user)
        published = self.api.patch(self.url, {"verdict": "fail"}, format="json")
        VerificationReport.objects.filter(pk=self.report.pk).update(
            status=VerificationReport.Status.SUBMITTED
        )
        submitted = self.api.patch(self.url, {"verdict": "partial"}, format="json")

        self.assertEqual(published.status_code, 403)
        self.assertEqual(submitted.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.verdict, "partial")

    def test_unpublished_reports_are_visible_only_to_their_tester_and_staff(self):
        VerificationReport.objects.filter(pk=self.report.pk).update(
            status=VerificationReport.Status.SUBMITTED
        )

        self.api.force_authenticate(self.other)
        stranger = self.api.get(self.url)
        self.api.force_authenticate(self.tester_user)
        owner = self.api.get(self.url)
        self.api.force_authenticate(self.staff)
        staff = self.api.get(self.url)

        self.assertEqual(stranger.status_code, 404)
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(staff.status_code, 200)

    def test_creating_a_report_sets_the_tester_and_requires_public_firmware(self):
        self.api.force_authenticate(self.tester_user)
        url = reverse("firmwares:verification-reports-list")
        hidden = OfficialFirmware.objects.create(
            original_file_name="hidden.zip",
            stored_file_path="/f/hidden.zip",
            is_verified=False,
        )

        created = self.api.post(
            url, {"firmware": str(self.firmware.pk), "verdict": "pass"}, format="json"
        )
        refused = self.api.post(
            url, {"firmware": str(hidden.pk), "verdict": "pass"}, format="json"
        )
        self.api.force_authenticate(self.user)
        not_a_tester = self.api.post(
            url, {"firmware": str(self.firmware.pk), "verdict": "pass"}, format="json"
        )

        self.assertEqual(created.status_code, 201, created.content)
        new_report = VerificationReport.objects.get(pk=created.json()["id"])
        self.assertEqual(new_report.tester, self.tester)
        self.assertEqual(new_report.status, VerificationReport.Status.SUBMITTED)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(not_a_tester.status_code, 403)
