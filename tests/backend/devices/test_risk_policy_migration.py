from __future__ import annotations

from typing import ClassVar

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RiskPolicyMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("devices", "0008_hash_device_event_ips_and_remove_raw_identifiers"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("devices", "0009_deterministic_risk_policy"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_risk_policy_flags_are_renamed_without_losing_values(self):
        old_apps = self.migrate(self.migrate_from)
        old_config = old_apps.get_model("devices", "DeviceConfig")
        old_policy = old_apps.get_model("devices", "AppPolicy")

        config, _ = old_config.objects.update_or_create(
            pk=1, defaults={"ai_risk_scoring_enabled": True}
        )
        policy = old_policy.objects.create(
            name="Migration policy",
            ai_risk_scoring=True,
        )

        new_apps = self.migrate(self.migrate_to)
        new_config = new_apps.get_model("devices", "DeviceConfig")
        new_policy = new_apps.get_model("devices", "AppPolicy")

        self.assertTrue(new_config.objects.get(pk=config.pk).risk_scoring_enabled)
        self.assertTrue(new_policy.objects.get(pk=policy.pk).risk_scoring_enabled)

        restored_apps = self.migrate(self.migrate_from)
        restored_config = restored_apps.get_model("devices", "DeviceConfig")
        restored_policy = restored_apps.get_model("devices", "AppPolicy")
        self.assertTrue(
            restored_config.objects.get(pk=config.pk).ai_risk_scoring_enabled
        )
        self.assertTrue(restored_policy.objects.get(pk=policy.pk).ai_risk_scoring)
