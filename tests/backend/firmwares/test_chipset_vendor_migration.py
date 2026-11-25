from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ChipsetVendorMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0012_remove_ai_field_naming"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("firmwares", "0013_normalize_chipset_vendors"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_vendor_aliases_share_canonical_record_and_reverse_restores_fk_label(self):
        old_apps = self.migrate(self.migrate_from)
        chipset_model = old_apps.get_model("firmwares", "Chipset")
        chipset_model.objects.create(
            vendor="Spreadtrum", name="SC9863A", slug="spreadtrum-sc9863a"
        )
        chipset_model.objects.create(
            vendor="RDA Microelectronics",
            name="RDA8810PL",
            slug="rda-rda8810pl",
        )

        new_apps = self.migrate(self.migrate_to)
        chipset_model = new_apps.get_model("firmwares", "Chipset")
        vendor_model = new_apps.get_model("firmwares", "ChipsetVendor")
        chipsets = list(chipset_model.objects.select_related("vendor").order_by("pk"))
        self.assertEqual([chipset.vendor.name for chipset in chipsets], ["Unisoc"] * 2)
        vendor = vendor_model.objects.get(name="Unisoc")
        self.assertEqual(set(vendor.aliases), {"Spreadtrum", "RDA Microelectronics"})

        restored_apps = self.migrate(self.migrate_from)
        restored = restored_apps.get_model("firmwares", "Chipset")
        self.assertEqual(
            set(restored.objects.values_list("vendor", flat=True)), {"Unisoc"}
        )

    def test_vendor_merge_collision_is_rejected_without_rewriting_rows(self):
        old_apps = self.migrate(self.migrate_from)
        chipset_model = old_apps.get_model("firmwares", "Chipset")
        chipset_model.objects.create(
            vendor="Unisoc", name="SC9863A", slug="unisoc-sc9863a"
        )
        chipset_model.objects.create(
            vendor="Spreadtrum", name="SC9863A", slug="spreadtrum-sc9863a"
        )

        executor = MigrationExecutor(connection)
        with self.assertRaisesRegex(RuntimeError, "would merge chipset rows"):
            executor.migrate(self.migrate_to)

        old_apps = (
            MigrationExecutor(connection).loader.project_state(self.migrate_from).apps
        )
        chipset_model = old_apps.get_model("firmwares", "Chipset")
        self.assertEqual(
            set(chipset_model.objects.values_list("vendor", flat=True)),
            {"Unisoc", "Spreadtrum"},
        )
        chipset_model.objects.filter(
            slug__in=("unisoc-sc9863a", "spreadtrum-sc9863a")
        ).delete()
