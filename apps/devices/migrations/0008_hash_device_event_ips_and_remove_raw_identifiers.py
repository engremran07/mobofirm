from typing import ClassVar

from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.operations.base import Operation
from django.utils.crypto import salted_hmac

IP_HASH_SALT = "apps.devices.client-ip.v1"


def _hash_ip(value):
    if not value:
        return ""
    return salted_hmac(IP_HASH_SALT, value.strip(), algorithm="sha256").hexdigest()


def hash_historical_event_ips(apps, schema_editor):
    database = schema_editor.connection.alias
    DeviceEvent = apps.get_model("devices", "DeviceEvent")
    batch = []
    rows = (
        DeviceEvent.objects.using(database)
        .exclude(ip="")
        .only("pk", "ip")
        .iterator(chunk_size=500)
    )
    for row in rows:
        row.ip_hash = _hash_ip(row.ip)
        batch.append(row)
        if len(batch) == 500:
            DeviceEvent.objects.using(database).bulk_update(batch, ["ip_hash"])
            batch.clear()
    if batch:
        DeviceEvent.objects.using(database).bulk_update(batch, ["ip_hash"])
    for model_name, field_name in (
        ("DeviceEvent", "metadata"),
        ("DeviceFingerprint", "signals"),
        ("RegistryEvent", "metadata"),
        ("BehaviorInsight", "metadata"),
    ):
        Model = apps.get_model("devices", model_name)
        batch = []
        rows = (
            Model.objects.using(database)
            .only("pk", field_name)
            .iterator(chunk_size=500)
        )
        for row in rows:
            original = getattr(row, field_name)
            redacted = _remove_raw_identifiers(original)
            if redacted != original:
                setattr(row, field_name, redacted)
                batch.append(row)
            if len(batch) == 500:
                Model.objects.using(database).bulk_update(batch, [field_name])
                batch.clear()
        if batch:
            Model.objects.using(database).bulk_update(batch, [field_name])


def _remove_raw_identifiers(value):
    sensitive_keys = {"ip", "ip_address", "remote_addr", "user_agent", "ua"}
    if isinstance(value, dict):
        return {
            key: _remove_raw_identifiers(item)
            for key, item in value.items()
            if str(key).casefold() not in sensitive_keys
        }
    if isinstance(value, list):
        return [_remove_raw_identifiers(item) for item in value]
    return value


def refuse_reverse(apps, schema_editor):
    raise IrreversibleError(
        "Raw device IP and User-Agent values were removed and cannot be restored."
    )


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("devices", "0007_os_credential_identity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations: ClassVar[list[Operation]] = [
        migrations.AddField(
            model_name="deviceevent",
            name="ip_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(hash_historical_event_ips, refuse_reverse),
        migrations.RemoveField(
            model_name="deviceevent",
            name="ip",
        ),
        migrations.RemoveField(
            model_name="deviceevent",
            name="user_agent",
        ),
        migrations.RemoveField(
            model_name="devicefingerprint",
            name="ip",
        ),
        migrations.RemoveField(
            model_name="registryevent",
            name="ip",
        ),
    ]
