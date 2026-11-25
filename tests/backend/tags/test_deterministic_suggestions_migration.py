from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DeterministicSuggestionsMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("tags", "0006_automation_setting_help"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("tags", "0007_deterministic_suggestions"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_suggestion_fields_and_setting_keep_existing_values(self):
        old_apps = self.migrate(self.migrate_from)
        old_tag = old_apps.get_model("tags", "Tag")
        old_settings = old_apps.get_model("tags", "TagsSettings")

        tag = old_tag.objects.create(
            name="Migration suggestion",
            ai_suggested=True,
            ai_score=12.5,
        )
        settings, _ = old_settings.objects.update_or_create(
            pk=1, defaults={"enable_ai_suggestions": False}
        )

        new_apps = self.migrate(self.migrate_to)
        new_tag = new_apps.get_model("tags", "Tag")
        new_settings = new_apps.get_model("tags", "TagsSettings")

        migrated_tag = new_tag.objects.get(pk=tag.pk)
        self.assertTrue(migrated_tag.is_suggested)
        self.assertEqual(migrated_tag.suggestion_score, 12.5)
        self.assertFalse(
            new_settings.objects.get(pk=settings.pk).automatic_suggestions_enabled
        )

        restored_apps = self.migrate(self.migrate_from)
        restored_tag = restored_apps.get_model("tags", "Tag")
        restored_settings = restored_apps.get_model("tags", "TagsSettings")
        self.assertTrue(restored_tag.objects.get(pk=tag.pk).ai_suggested)
        self.assertEqual(restored_tag.objects.get(pk=tag.pk).ai_score, 12.5)
        self.assertFalse(
            restored_settings.objects.get(pk=settings.pk).enable_ai_suggestions
        )
