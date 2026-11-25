from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DeterministicAdsFieldsMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ads", "0015_alter_adplacement_zone"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ads", "0016_deterministic_ads_fields"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_renamed_product_and_scan_fields_preserve_existing_data(self):
        old_apps = self.migrate(self.migrate_from)
        provider_model = old_apps.get_model("ads", "AffiliateProvider")
        product_model = old_apps.get_model("ads", "AffiliateProduct")
        scan_model = old_apps.get_model("ads", "AutoAdsScanResult")

        provider = provider_model.objects.create(
            name="Migration provider", provider_type="custom"
        )
        product = product_model.objects.create(
            provider_id=provider.pk,
            name="Migration product",
            slug="migration-product",
            external_id="migration-product",
            product_url="https://example.test/migration-product",
            ai_relevance_score=Decimal("73.50"),
        )
        scan = scan_model.objects.create(
            template_path="migration/template.html",
            ai_recommendation="Template scan finding",
        )

        new_apps = self.migrate(self.migrate_to)
        migrated_product = new_apps.get_model("ads", "AffiliateProduct")
        migrated_scan = new_apps.get_model("ads", "AutoAdsScanResult")

        self.assertEqual(
            migrated_product.objects.get(pk=product.pk).relevance_score,
            Decimal("73.50"),
        )
        self.assertEqual(
            migrated_scan.objects.get(pk=scan.pk).recommendation,
            "Template scan finding",
        )

        restored_apps = self.migrate(self.migrate_from)
        restored_product = restored_apps.get_model("ads", "AffiliateProduct")
        restored_scan = restored_apps.get_model("ads", "AutoAdsScanResult")
        self.assertEqual(
            restored_product.objects.get(pk=product.pk).ai_relevance_score,
            Decimal("73.50"),
        )
        self.assertEqual(
            restored_scan.objects.get(pk=scan.pk).ai_recommendation,
            "Template scan finding",
        )
