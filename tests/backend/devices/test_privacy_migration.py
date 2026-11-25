from __future__ import annotations

from importlib import import_module
from typing import ClassVar
from unittest.mock import patch

from django.core.exceptions import FieldDoesNotExist
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils.crypto import salted_hmac


class DevicePrivacyMigrationTests(TransactionTestCase):
    migrate_from: ClassVar[tuple[tuple[str, str], ...]] = (
        ("devices", "0007_os_credential_identity"),
    )
    migrate_to: ClassVar[tuple[tuple[str, str], ...]] = (
        ("devices", "0008_hash_device_event_ips_and_remove_raw_identifiers"),
    )

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_ips_are_hashed_and_raw_metadata_is_removed(self):
        migration_module = import_module(
            "apps.devices.migrations.0008_hash_device_event_ips_and_remove_raw_identifiers"
        )
        data_migration = next(
            operation
            for operation in migration_module.Migration.operations
            if isinstance(operation, migrations.RunPython)
        )
        with patch.object(data_migration, "reverse_code", migrations.RunPython.noop):
            old_apps = self.migrate(self.migrate_from)
        DeviceEvent = old_apps.get_model("devices", "DeviceEvent")
        DeviceFingerprint = old_apps.get_model("devices", "DeviceFingerprint")
        RegistryEvent = old_apps.get_model("devices", "RegistryEvent")
        BehaviorInsight = old_apps.get_model("devices", "BehaviorInsight")

        event = DeviceEvent.objects.create(
            event_type="login",
            ip="203.0.113.7",
            user_agent="Example browser",
            metadata={"client": {"ip": "203.0.113.9", "user_agent": "Example"}},
        )
        fingerprint = DeviceFingerprint.objects.create(
            fingerprint_hash="legacy-fingerprint",
            ip="203.0.113.8",
            signals={"ip": "203.0.113.10", "ua": "Example"},
        )
        registry_event = RegistryEvent.objects.create(
            fingerprint=fingerprint,
            action="visit",
            ip="203.0.113.11",
            metadata={"remote_addr": "203.0.113.12"},
        )
        insight = BehaviorInsight.objects.create(
            metadata={"details": [{"ip_address": "203.0.113.13"}]}
        )

        new_apps = self.migrate(self.migrate_to)
        DeviceEvent = new_apps.get_model("devices", "DeviceEvent")
        DeviceFingerprint = new_apps.get_model("devices", "DeviceFingerprint")
        RegistryEvent = new_apps.get_model("devices", "RegistryEvent")
        BehaviorInsight = new_apps.get_model("devices", "BehaviorInsight")

        expected_hash = salted_hmac(
            "apps.devices.client-ip.v1",
            "203.0.113.7",
            algorithm="sha256",
        ).hexdigest()
        migrated_event = DeviceEvent.objects.get(pk=event.pk)
        migrated_fingerprint = DeviceFingerprint.objects.get(pk=fingerprint.pk)
        migrated_registry_event = RegistryEvent.objects.get(pk=registry_event.pk)
        migrated_insight = BehaviorInsight.objects.get(pk=insight.pk)

        self.assertEqual(migrated_event.ip_hash, expected_hash)
        self.assertEqual(migrated_event.metadata, {"client": {}})
        self.assertEqual(migrated_fingerprint.signals, {})
        self.assertEqual(migrated_registry_event.metadata, {})
        self.assertEqual(migrated_insight.metadata, {"details": [{}]})
        for model in (
            DeviceEvent,
            DeviceFingerprint,
            RegistryEvent,
        ):
            with self.assertRaises(FieldDoesNotExist):
                model._meta.get_field("ip")
        for model in (DeviceFingerprint, RegistryEvent):
            with self.assertRaises(FieldDoesNotExist):
                model._meta.get_field("ip_hash")
        with self.assertRaises(FieldDoesNotExist):
            DeviceEvent._meta.get_field("user_agent")
