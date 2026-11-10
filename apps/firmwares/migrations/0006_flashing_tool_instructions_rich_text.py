"""Flashing tool instructions are now written with the rich-text editor."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Only the field's help text changes; no data is touched."""

    dependencies = (("firmwares", "0005_variant_identity"),)

    operations = (
        migrations.AlterField(
            model_name="flashingtool",
            name="instructions",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Usage instructions, formatted with the editor",
            ),
        ),
    )
