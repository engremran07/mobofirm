from __future__ import annotations

import tempfile
from pathlib import Path

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import (
    Brand,
    BrandCreationRequest,
    ModelCreationRequest,
    OfficialFirmware,
)
from apps.firmwares.services import handle_upload

DIALOG = {"_form_submitted": "on"}


class FirmwareAdminActionTestCase(TestCase):
    def setUp(self) -> None:
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        override = override_settings(FIRMWARE_STORAGE_ROOT=Path(storage.name))
        override.enable()
        self.addCleanup(override.disable)
        user_model = get_user_model()
        self.admin_user = user_model.objects.create_superuser(
            username="unfold-reviewer", email="unfold-reviewer@example.test"
        )
        self.uploader = user_model.objects.create_user(
            username="unfold-uploader", email="unfold-uploader@example.test"
        )
        self.client = Client()
        self.client.force_login(self.admin_user)
        self.pending = handle_upload(
            uploader=self.uploader,
            uploaded_brand=None,
            uploaded_model=None,
            uploaded_variant=None,
            file_obj=SimpleUploadedFile("unfold.zip", b"unfold-bytes"),
            is_password_protected=False,
            password="",
        )

    def action_url(self, name: str) -> str:
        return reverse(
            f"admin:firmwares_pendingfirmware_{name}", args=[self.pending.pk]
        )


class PendingFirmwareDialogActionTests(FirmwareAdminActionTestCase):
    def test_dialog_is_rendered_before_anything_changes(self) -> None:
        response = self.client.get(self.action_url("approve_upload"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Firmware category")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")

    def test_approve_dialog_routes_the_upload_through_the_service(self) -> None:
        response = self.client.post(
            self.action_url("approve_upload"),
            {**DIALOG, "category": "official", "notes": "Looks right"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Redirect", response.headers)
        self.pending.refresh_from_db()
        record = OfficialFirmware.objects.get()
        self.assertEqual(self.pending.admin_decision, "approved")
        self.assertEqual(self.pending.reviewed_by, self.admin_user)
        self.assertEqual(record.reviewed_by, self.admin_user)
        self.assertTrue(record.is_verified)

    def test_approve_dialog_rejects_a_missing_category(self) -> None:
        response = self.client.post(
            self.action_url("approve_upload"), {**DIALOG, "notes": "x"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Redirect", response.headers)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_reject_dialog_requires_and_stores_a_reason(self) -> None:
        missing = self.client.post(self.action_url("reject_upload"), DIALOG)
        self.assertNotIn("HX-Redirect", missing.headers)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")

        done = self.client.post(
            self.action_url("reject_upload"), {**DIALOG, "reason": "Duplicate upload"}
        )

        self.assertIn("HX-Redirect", done.headers)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "rejected")
        self.assertEqual(self.pending.admin_notes, "Duplicate upload")

    def test_withdraw_dialog_returns_an_approved_upload_to_pending(self) -> None:
        self.client.post(
            self.action_url("approve_upload"), {**DIALOG, "category": "official"}
        )

        self.client.post(
            self.action_url("withdraw_upload"), {**DIALOG, "reason": "Bad build"}
        )

        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")
        self.assertFalse(OfficialFirmware.objects.exists())

    def test_withdrawing_a_pending_upload_surfaces_the_service_error(self) -> None:
        response = self.client.post(
            self.action_url("withdraw_upload"), {**DIALOG, "reason": "Nope"}
        )

        texts = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("Only approved uploads" in text for text in texts))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")

    def test_staff_without_change_permission_cannot_use_the_dialog_actions(
        self,
    ) -> None:
        viewer = get_user_model().objects.create_user(
            username="unfold-viewer", email="unfold-viewer@example.test", is_staff=True
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_pendingfirmware")
        )
        client = Client()
        client.force_login(viewer)

        response = client.post(
            self.action_url("approve_upload"), {**DIALOG, "category": "official"}
        )

        self.assertEqual(response.status_code, 403)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.admin_decision, "pending")

    def test_unknown_upload_returns_404(self) -> None:
        url = reverse(
            "admin:firmwares_pendingfirmware_reject_upload",
            args=["00000000-0000-0000-0000-000000000000"],
        )

        response = self.client.post(url, {**DIALOG, "reason": "x"})

        self.assertEqual(response.status_code, 404)


class CatalogueRequestActionTests(FirmwareAdminActionTestCase):
    def test_brand_request_approval_creates_the_brand_once(self) -> None:
        request_row = BrandCreationRequest.objects.create(
            name="Unfold Brand", requested_by=self.uploader
        )
        url = reverse(
            "admin:firmwares_brandcreationrequest_approve_request",
            args=[request_row.pk],
        )

        first = self.client.post(url, DIALOG)
        second = self.client.post(url, DIALOG)

        self.assertIn("HX-Redirect", first.headers)
        self.assertIn("HX-Redirect", second.headers)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status, "approved")
        self.assertEqual(request_row.reviewed_by, self.admin_user)
        self.assertIsNotNone(request_row.reviewed_at)
        self.assertEqual(Brand.objects.filter(slug="unfold-brand").count(), 1)

    def test_brand_request_with_a_reserved_slug_is_reported_not_created(self) -> None:
        from apps.firmwares.constants import RESERVED_BRAND_SLUGS

        reserved = RESERVED_BRAND_SLUGS[0]
        request_row = BrandCreationRequest.objects.create(name=reserved)
        url = reverse(
            "admin:firmwares_brandcreationrequest_approve_request",
            args=[request_row.pk],
        )

        self.client.post(url, DIALOG)

        request_row.refresh_from_db()
        self.assertEqual(request_row.status, "pending")
        self.assertFalse(Brand.objects.filter(slug=reserved).exists())

    def test_reject_request_stores_the_reason(self) -> None:
        brand = Brand.objects.create(name="Req Brand", slug="req-brand")
        request_row = ModelCreationRequest.objects.create(brand=brand, name="Ghost")
        url = reverse(
            "admin:firmwares_modelcreationrequest_reject_request",
            args=[request_row.pk],
        )

        missing = self.client.post(url, DIALOG)
        self.assertNotIn("HX-Redirect", missing.headers)
        self.client.post(url, {**DIALOG, "reason": "Not a real device"})

        request_row.refresh_from_db()
        self.assertEqual(request_row.status, "rejected")
        self.assertEqual(request_row.notes, "Not a real device")


class FirmwareAdminPagesTests(FirmwareAdminActionTestCase):
    def test_every_firmware_model_changelist_and_add_form_render(self) -> None:
        request = RequestFactory().get("/")
        request.user = self.admin_user
        for model in django_apps.get_app_config("firmwares").get_models():
            if not admin.site.is_registered(model):
                continue
            model_admin = admin.site.get_model_admin(model)
            opts = model._meta
            changelist = reverse(f"admin:firmwares_{opts.model_name}_changelist")
            with self.subTest(model=opts.label, page="changelist"):
                self.assertEqual(self.client.get(changelist).status_code, 200)
            if model_admin.has_add_permission(request):
                add = reverse(f"admin:firmwares_{opts.model_name}_add")
                with self.subTest(model=opts.label, page="add"):
                    self.assertEqual(self.client.get(add).status_code, 200)

    def test_review_states_render_as_badges_without_inline_styles(self) -> None:
        response = self.client.get(
            reverse("admin:firmwares_pendingfirmware_changelist")
        )

        self.assertContains(response, "unfold.zip")
        self.assertNotContains(response, 'style="background-color')
