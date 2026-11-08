from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("core", "0006_wallet_referral"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.AddField(
            model_name="appregistry",
            name="promotions_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
