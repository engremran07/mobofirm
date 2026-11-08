from typing import Any, ClassVar

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list[Any]] = [
        ("blog", "0006_postlike"),
        ("devices", "0005_remove_device_device_user_os_fingerprint_unique_and_more"),
        ("firmwares", "0001_initial"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AddField(
            model_name="generatedflashingguide",
            name="post",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flashing_guide_source",
                to="blog.post",
            ),
        ),
        migrations.AddField(
            model_name="automatedbloglink",
            name="post",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="firmware_automation_link",
                to="blog.post",
            ),
        ),
        migrations.AddField(
            model_name="verificationreport",
            name="device",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="verification_reports",
                to="devices.device",
            ),
        ),
    ]
