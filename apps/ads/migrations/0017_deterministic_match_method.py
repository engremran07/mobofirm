from typing import ClassVar

from django.db import migrations, models


def rename_ai_match_type(apps, schema_editor):
    product_match = apps.get_model("ads", "AffiliateProductMatch")
    product_match.objects.using(schema_editor.connection.alias).filter(
        match_type="ai"
    ).update(match_type="recommended")


def restore_ai_match_type(apps, schema_editor):
    product_match = apps.get_model("ads", "AffiliateProductMatch")
    product_match.objects.using(schema_editor.connection.alias).filter(
        match_type="recommended"
    ).update(match_type="ai")


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("ads", "0016_deterministic_ads_fields"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(rename_ai_match_type, restore_ai_match_type),
        migrations.AlterField(
            model_name="affiliateproductmatch",
            name="match_type",
            field=models.CharField(
                choices=[
                    ("auto", "Auto-matched"),
                    ("manual", "Manually assigned"),
                    ("recommended", "Deterministically recommended"),
                ],
                default="auto",
                max_length=20,
            ),
        ),
    ]
