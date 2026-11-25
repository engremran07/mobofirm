from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DeviceTelemetryMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("devices", "0009_deterministic_risk_policy"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        (
            "devices",
            "0010_delete_apppolicy_remove_behaviorinsight_fingerprint_and_more",
        ),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_policies_are_preserved_while_fingerprint_telemetry_is_removed(self):
        old_apps = self.migrate(self.migrate_from)
        old_policy_model = old_apps.get_model("devices", "AppPolicy")
        policy = old_policy_model.objects.create(
            name="legacy-login",
            device_locking_mode="strict",
            mfa_requirement="always",
            risk_scoring_enabled=True,
            service_level_rules={"login": {"max_devices": 2}},
            monthly_device_quota=4,
            yearly_device_quota=12,
        )

        new_apps = self.migrate(self.migrate_to)
        config_model = new_apps.get_model("devices", "DeviceConfig")
        saved = config_model.objects.get(pk=1).app_policies[policy.name]
        self.assertEqual(
            saved,
            {
                "device_locking_mode": "strict",
                "mfa_requirement": "always",
                "risk_scoring_enabled": True,
                "service_level_rules": {"login": {"max_devices": 2}},
                "monthly_device_quota": 4,
                "yearly_device_quota": 12,
            },
        )

        for removed_model in (
            "AppPolicy",
            "BehaviorInsight",
            "DeviceFingerprint",
            "RegistryEvent",
            "TrustScore",
        ):
            with self.subTest(model=removed_model), self.assertRaises(LookupError):
                new_apps.get_model("devices", removed_model)

        device_model = new_apps.get_model("devices", "Device")
        for removed_field in (
            "os_fingerprint",
            "hardware_uuid",
            "device_key_id",
            "key_algorithm",
            "public_key",
            "fingerprint_hash",
        ):
            with (
                self.subTest(field=removed_field),
                self.assertRaises(FieldDoesNotExist),
            ):
                device_model._meta.get_field(removed_field)

        restored_apps = self.migrate(self.migrate_from)
        restored_policy_model = restored_apps.get_model("devices", "AppPolicy")
        restored = restored_policy_model.objects.get(name=policy.name)
        self.assertEqual(restored.device_locking_mode, policy.device_locking_mode)
        self.assertEqual(restored.mfa_requirement, policy.mfa_requirement)
        self.assertEqual(restored.service_level_rules, policy.service_level_rules)
        self.assertEqual(restored.monthly_device_quota, policy.monthly_device_quota)
        self.assertEqual(restored.yearly_device_quota, policy.yearly_device_quota)
