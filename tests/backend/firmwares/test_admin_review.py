from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import (
    Brand,
    Chipset,
    DownloadToken,
    Model,
    OfficialFirmware,
    PendingFirmware,
)
from apps.firmwares.services import handle_upload


class FirmwareAdminTests(TestCase):
    def setUp(self):
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        override = override_settings(FIRMWARE_STORAGE_ROOT=Path(storage.name))
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="admin-reviewer", email="admin-reviewer@example.test"
        )
        self.uploader = user_model.objects.create_user(
            username="admin-uploader", email="admin-uploader@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin_user)
        self.pending = handle_upload(
            uploader=self.uploader,
            uploaded_brand=None,
            uploaded_model=None,
            uploaded_variant=None,
            file_obj=SimpleUploadedFile("admin.zip", b"admin-bytes"),
            is_password_protected=False,
            password=None,
        )
        self.changelist = reverse("admin:firmwares_pendingfirmware_changelist")

    def run_action(self, action, **extra):
        data = {
            "action": action,
            "_selected_action": [str(self.pending.pk)],
            "index": "0",
        }
        data.update(extra)
        return self.client.post(self.changelist, data, follow=True)

    def test_approving_requires_an_explicit_category(self):
        response = self.run_action("approve_selected", category="", notes="ok")

        self.assertContains(response, "Choose a firmware category")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_approve_action_routes_the_upload_and_records_the_reviewer(self):
        self.run_action("approve_selected", category="official", notes="Looks right")

        self.pending.refresh_from_db()
        record = OfficialFirmware.objects.get()
        self.assertEqual(self.pending.admin_decision, "approved")
        self.assertEqual(self.pending.reviewed_by, self.admin_user)
        self.assertEqual(self.pending.admin_notes, "Looks right")
        self.assertTrue(record.is_verified)
        self.assertEqual(record.reviewed_by, self.admin_user)

    def test_reject_action_needs_a_reason(self):
        missing = self.run_action("reject_selected", notes="")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertContains(missing, "A reason is required")

        self.run_action("reject_selected", notes="Duplicate upload")

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "rejected")
        self.assertEqual(self.pending.admin_notes, "Duplicate upload")

    def test_withdraw_action_returns_an_approved_upload_to_pending(self):
        self.run_action("approve_selected", category="official")

        self.run_action("withdraw_selected", notes="Bad build")

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_staff_without_the_change_permission_cannot_review(self):
        reviewer_less = get_user_model().objects.create_user(
            username="view-only", email="view-only@example.test", is_staff=True
        )
        from django.contrib.auth.models import Permission

        reviewer_less.user_permissions.add(
            Permission.objects.get(codename="view_pendingfirmware")
        )
        client = Client()
        client.force_login(reviewer_less)

        response = client.post(
            self.changelist,
            {
                "action": "approve_selected",
                "_selected_action": [str(self.pending.pk)],
                "category": "official",
                "index": "0",
            },
            follow=True,
        )

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertEqual(response.status_code, 200)

    def test_pending_firmware_cannot_be_added_by_hand(self):
        response = self.client.get(reverse("admin:firmwares_pendingfirmware_add"))

        self.assertEqual(response.status_code, 403)

    def test_typed_firmware_cannot_be_added_and_review_fields_are_read_only(self):
        brand = Brand.objects.create(name="Admin Brand", slug="admin-brand")
        model = Model.objects.create(
            brand=brand, name="Admin Phone", slug="admin-phone"
        )
        firmware = OfficialFirmware.objects.create(
            original_file_name="admin-record.zip",
            stored_file_path="/f/admin-record.zip",
            brand=brand,
            model=model,
        )

        add = self.client.get(reverse("admin:firmwares_officialfirmware_add"))
        change = self.client.get(
            reverse("admin:firmwares_officialfirmware_change", args=[firmware.pk])
        )
        forged = self.client.post(
            reverse("admin:firmwares_officialfirmware_change", args=[firmware.pk]),
            {
                "original_file_name": "admin-record.zip",
                "is_verified": "on",
                "stored_file_path": "https://evil.example.test/x.zip",
                "is_active": "on",
                "package_format": "unknown",
                "brand": brand.pk,
                "model": model.pk,
                "partitions": "[]",
                "metadata": "{}",
            },
        )

        self.assertEqual(add.status_code, 403)
        self.assertEqual(change.status_code, 200)
        self.assertNotContains(change, 'name="is_verified"')
        self.assertNotContains(change, 'name="stored_file_path"')
        firmware.refresh_from_db()
        self.assertFalse(firmware.is_verified)
        self.assertEqual(firmware.stored_file_path, "/f/admin-record.zip")
        self.assertIn(forged.status_code, (200, 302))

    def test_deactivating_firmware_from_the_admin_hides_it(self):
        brand = Brand.objects.create(name="Toggle Brand", slug="toggle-brand")
        firmware = OfficialFirmware.objects.create(
            original_file_name="toggle.zip",
            stored_file_path="/f/toggle.zip",
            brand=brand,
            is_verified=True,
        )

        self.client.post(
            reverse("admin:firmwares_officialfirmware_changelist"),
            {
                "action": "deactivate_selected",
                "_selected_action": [str(firmware.pk)],
                "index": "0",
            },
        )

        firmware.refresh_from_db()
        self.assertFalse(firmware.is_active)

    def test_new_admin_pages_render(self):
        chipset = Chipset.objects.create(
            vendor="MediaTek", name="Helio G99", slug="mediatek-helio-g99"
        )
        brand = Brand.objects.create(name="Page Brand", slug="page-brand")
        firmware = OfficialFirmware.objects.create(
            original_file_name="page.zip", stored_file_path="/f/page.zip", brand=brand
        )
        token = DownloadToken.objects.create(
            firmware=firmware, token="admin-token", expires_at=firmware.created_at
        )

        urls = [
            reverse("admin:firmwares_chipset_changelist"),
            reverse("admin:firmwares_chipset_change", args=[chipset.pk]),
            reverse("admin:firmwares_automatedbloglink_changelist"),
            reverse("admin:firmwares_downloadtoken_changelist"),
            reverse("admin:firmwares_downloadtoken_change", args=[token.pk]),
            reverse("admin:firmwares_pendingfirmware_change", args=[self.pending.pk]),
            reverse("admin:firmwares_flashingtool_add"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(
            PendingFirmware.objects.filter(admin_decision="approved").exists()
        )
