from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ModelVariantCodesMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0014_engineeringfirmware_variant_code_and_more"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0015_model_variant_codes"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_model_and_variant_codes_are_backfilled(self):
        old_apps = self.migrate(self.migrate_from)
        Brand = old_apps.get_model("firmwares", "Brand")
        Model = old_apps.get_model("firmwares", "Model")
        Variant = old_apps.get_model("firmwares", "Variant")

        brand = Brand.objects.create(name="Code Migration Brand", slug="code-migration")
        model = Model.objects.create(
            brand=brand,
            name="Migration Device",
            slug="migration-device",
            model_code=" sm-a ",
            model_codes=["SM-B", "sm-a", " "],
        )
        Variant.objects.create(
            model=model,
            name="Global",
            slug="global",
            model_code=" sm-c ",
        )
        Variant.objects.create(model=model, name="No code", slug="no-code")

        new_apps = self.migrate(self.migrate_to)
        Model = new_apps.get_model("firmwares", "Model")
        self.assertEqual(
            Model.objects.get(pk=model.pk).variant_codes,
            ["SM-A", "SM-B", "SM-C"],
        )

        restored_apps = self.migrate(self.migrate_from)
        Model = restored_apps.get_model("firmwares", "Model")
        restored = Model.objects.get(pk=model.pk)
        self.assertEqual(restored.model_code, " sm-a ")
        self.assertEqual(restored.model_codes, ["SM-B", "sm-a", " "])
