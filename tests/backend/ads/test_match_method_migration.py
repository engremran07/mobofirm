from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MatchMethodMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ads", "0016_deterministic_ads_fields"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("ads", "0017_deterministic_match_method"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_recommendation_assignment_is_renamed_reversibly(self):
        old_apps = self.migrate(self.migrate_from)
        provider = old_apps.get_model("ads", "AffiliateProvider").objects.create(
            name="Migration provider",
            provider_type="custom",
        )
        product = old_apps.get_model("ads", "AffiliateProduct").objects.create(
            provider_id=provider.pk,
            name="Migration product",
            slug="migration-product",
            external_id="migration-product",
            product_url="https://example.test/migration-product",
        )
        product_match = old_apps.get_model(
            "ads", "AffiliateProductMatch"
        ).objects.create(
            product_id=product.pk,
            match_type="ai",
        )

        new_apps = self.migrate(self.migrate_to)
        migrated_match = new_apps.get_model("ads", "AffiliateProductMatch").objects.get(
            pk=product_match.pk
        )
        self.assertEqual(migrated_match.match_type, "recommended")

        restored_apps = self.migrate(self.migrate_from)
        restored_match = restored_apps.get_model(
            "ads", "AffiliateProductMatch"
        ).objects.get(pk=product_match.pk)
        self.assertEqual(restored_match.match_type, "ai")
