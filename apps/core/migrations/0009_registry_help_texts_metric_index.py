from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive: help texts for two registry flags and one redundant index removed.

    ``Metric.name`` was indexed on its own and again as the leading column of the
    composite ``(name, timestamp)`` index; the composite one serves every lookup.
    """

    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("core", "0008_searchsettings"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AlterField(
            model_name="appregistry",
            name="devices_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Kept for completeness only: device registration and enforcement "
                    "are never switched off by this flag. Use 'device identity' to "
                    "pause automatic registration."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="appregistry",
            name="device_identity_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Register a device automatically when a signed-in visitor arrives."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="metric",
            name="name",
            field=models.CharField(max_length=200),
        ),
    ]
