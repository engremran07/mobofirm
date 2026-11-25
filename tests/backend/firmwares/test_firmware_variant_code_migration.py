from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FirmwareVariantCodeMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0013_normalize_chipset_vendors"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0014_engineeringfirmware_variant_code_and_more"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_firmware_variant_codes_are_backfilled_and_reversible(self):
        old_apps = self.migrate(self.migrate_from)
        Brand = old_apps.get_model("firmwares", "Brand")
        Model = old_apps.get_model("firmwares", "Model")
        Variant = old_apps.get_model("firmwares", "Variant")
        Firmware = old_apps.get_model("firmwares", "OfficialFirmware")
        PendingFirmware = old_apps.get_model("firmwares", "PendingFirmware")

        brand = Brand.objects.create(name="Migration Brand", slug="migration-brand")
        model = Model.objects.create(
            brand=brand,
            name="Migration Device",
            slug="migration-device",
            model_code=" sm-def ",
        )
        variant = Variant.objects.create(
            model=model,
            name="Global",
            slug="global",
            model_code=" sm-abc ",
        )
        firmware = Firmware.objects.create(
            original_file_name="migration.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/migration.zip",
            model=model,
            variant=variant,
        )
        pending = PendingFirmware.objects.create(
            original_file_name="pending.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/pending.zip",
            uploaded_model=model,
            uploaded_variant=variant,
        )
        fallback = Firmware.objects.create(
            original_file_name="fallback.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/fallback.zip",
            model=model,
        )
        empty_variant = Variant.objects.create(
            model=model,
            name="No code",
            slug="no-code",
        )
        variant_fallback = Firmware.objects.create(
            original_file_name="variant-fallback.zip",
            source_type="external",
            external_url="https://downloads.example.invalid/variant-fallback.zip",
            model=model,
            variant=empty_variant,
        )

        new_apps = self.migrate(self.migrate_to)
        Firmware = new_apps.get_model("firmwares", "OfficialFirmware")
        PendingFirmware = new_apps.get_model("firmwares", "PendingFirmware")
        self.assertEqual(
            Firmware.objects.get(pk=firmware.pk).variant_code,
            "SM-ABC",
        )
        self.assertEqual(
            PendingFirmware.objects.get(pk=pending.pk).variant_code,
            "SM-ABC",
        )
        self.assertEqual(
            Firmware.objects.get(pk=fallback.pk).variant_code,
            "SM-DEF",
        )
        self.assertEqual(
            Firmware.objects.get(pk=variant_fallback.pk).variant_code,
            "SM-DEF",
        )

        restored_apps = self.migrate(self.migrate_from)
        Firmware = restored_apps.get_model("firmwares", "OfficialFirmware")
        restored = Firmware.objects.get(pk=firmware.pk)
        self.assertEqual(restored.variant_id, variant.pk)
        self.assertEqual(restored.model_id, model.pk)
