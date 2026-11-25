from __future__ import annotations

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GeneratedMetadataMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("seo", "0005_sitemap_robots_wellknown"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("seo", "0006_remove_ai_artifacts"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_generation_flag_is_preserved_and_unused_vectors_are_removed(self):
        old_apps = self.migrate(self.migrate_from)
        old_seo_model = old_apps.get_model("seo", "SEOModel")
        old_linkable_entity = old_apps.get_model("seo", "LinkableEntity")
        content_type = ContentType.objects.get_for_model(get_user_model())

        metadata = old_seo_model.objects.create(
            content_type_id=content_type.pk,
            object_id="7",
            ai_generated=True,
        )
        entity = old_linkable_entity.objects.create(
            content_type_id=content_type.pk,
            object_id="7",
            title="Migration entity",
            url="https://example.test/migration-entity",
            embedding=b"derived-embedding",
            vector=b"derived-vector",
        )

        new_apps = self.migrate(self.migrate_to)
        migrated_seo_model = new_apps.get_model("seo", "SEOModel")
        migrated_linkable_entity = new_apps.get_model("seo", "LinkableEntity")

        self.assertTrue(migrated_seo_model.objects.get(pk=metadata.pk).is_generated)
        field_names = {
            field.name for field in migrated_linkable_entity._meta.get_fields()
        }
        self.assertNotIn("embedding", field_names)
        self.assertNotIn("vector", field_names)
        self.assertEqual(
            migrated_linkable_entity.objects.get(pk=entity.pk).title,
            "Migration entity",
        )

        restored_apps = self.migrate(self.migrate_from)
        restored_seo_model = restored_apps.get_model("seo", "SEOModel")
        self.assertTrue(restored_seo_model.objects.get(pk=metadata.pk).ai_generated)
