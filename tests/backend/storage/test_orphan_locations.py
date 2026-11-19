"""A storage location whose firmware cannot be resolved must not crash the page.

Regression for the crawl 500 at ``/storage/`` ("'NoneType' object has no attribute
'_base_manager'"): a stale ``ContentType`` (its model was removed/renamed) makes the
generic relation resolve ``model_class()`` to ``None``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.firmwares.models import OfficialFirmware
from apps.storage import providers, services
from apps.storage.models import FirmwareStorageLocation


class OrphanLocationTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        override = override_settings(FIRMWARE_STORAGE_ROOT=root)
        override.enable()
        self.addCleanup(override.disable)
        (root / "gone.bin").write_bytes(b"x")
        self.staff = get_user_model().objects.create_user(
            username="orphan_storage_staff",
            email="orphan-storage-staff@example.com",
            password="Pass-12345!",
            is_staff=True,
        )
        self.client = Client()
        self.client.force_login(self.staff)

    def _stale_location(self) -> FirmwareStorageLocation:
        stale = ContentType.objects.create(
            app_label="removed_app", model="removedfirmware"
        )
        self.assertIsNone(stale.model_class())
        return FirmwareStorageLocation.objects.create(
            content_type=stale,
            object_id="12345678-aaaa-bbbb-cccc-1234567890ab",
            provider=providers.LOCAL,
            relative_path="gone.bin",
        )

    def test_status_page_renders_a_location_with_a_stale_content_type(self):
        self._stale_location()

        response = self.client.get(reverse("storage:status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "record missing")

    def test_admin_open_status_action_lands_on_the_status_page(self):
        self._stale_location()
        admin_client = Client()
        admin_client.force_login(
            get_user_model().objects.create_superuser(
                username="orphan_storage_root",
                email="orphan-storage-root@example.com",
                password="Pass-12345!",
            )
        )

        response = admin_client.get(
            reverse("admin:storage_firmwarestoragelocation_open_status"),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "record missing")

    def test_status_page_renders_a_location_with_a_malformed_object_id(self):
        location = FirmwareStorageLocation.objects.create(
            content_type=ContentType.objects.get_for_model(OfficialFirmware),
            object_id="not-a-uuid",
            provider=providers.LOCAL,
            relative_path="gone.bin",
        )
        self.assertIsNone(location.resolve_firmware())

        response = self.client.get(reverse("storage:status"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "record missing")

    def test_verifying_an_orphan_location_records_a_result_instead_of_failing(self):
        location = self._stale_location()

        verified = services.verify_location(location)

        self.assertEqual(verified.status, FirmwareStorageLocation.Status.AVAILABLE)
        self.assertIsNotNone(verified.last_verified_at)
