from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FirmwareReviewFieldMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0011_firmwarelinkreport"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0012_remove_ai_field_naming"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_request_and_submission_suggestions_are_preserved(self):
        old_apps = self.migrate(self.migrate_from)
        old_brand_request = old_apps.get_model("firmwares", "BrandCreationRequest")
        old_pending_firmware = old_apps.get_model("firmwares", "PendingFirmware")

        request = old_brand_request.objects.create(
            name="Migration brand",
            ai_suggestion={"source": "historical"},
        )
        pending = old_pending_firmware.objects.create(
            original_file_name="migration.zip",
            stored_file_path="pending/migration.zip",
            ai_brand="Migration brand",
            ai_model="Migration model",
            ai_variant="M-100",
            ai_category="official",
            ai_subtype="release",
        )

        new_apps = self.migrate(self.migrate_to)
        migrated_request = new_apps.get_model(
            "firmwares", "BrandCreationRequest"
        ).objects.get(pk=request.pk)
        migrated_pending = new_apps.get_model(
            "firmwares", "PendingFirmware"
        ).objects.get(pk=pending.pk)
        self.assertEqual(migrated_request.review_suggestion, {"source": "historical"})
        self.assertEqual(migrated_pending.suggested_brand, "Migration brand")
        self.assertEqual(migrated_pending.suggested_model, "Migration model")
        self.assertEqual(migrated_pending.suggested_variant, "M-100")
        self.assertEqual(migrated_pending.suggested_category, "official")
        self.assertEqual(migrated_pending.suggested_subtype, "release")

        restored_apps = self.migrate(self.migrate_from)
        restored_request = restored_apps.get_model(
            "firmwares", "BrandCreationRequest"
        ).objects.get(pk=request.pk)
        restored_pending = restored_apps.get_model(
            "firmwares", "PendingFirmware"
        ).objects.get(pk=pending.pk)
        self.assertEqual(restored_request.ai_suggestion, {"source": "historical"})
        self.assertEqual(restored_pending.ai_brand, "Migration brand")
        self.assertEqual(restored_pending.ai_category, "official")
