"""
FINDING-0010 — Encrypt AdNetwork and AffiliateProvider API credentials at rest.
Fields changed from CharField(max_length=255) to TextField() to accommodate
Fernet ciphertext (which is longer than raw credentials).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("ads", "0004_alter_autoadsscanresult_options_and_more"),)

    operations = (
        migrations.AlterField(
            model_name="adnetwork",
            name="api_key",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Fernet-encrypted API key.",
            ),
        ),
        migrations.AlterField(
            model_name="adnetwork",
            name="api_secret",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Fernet-encrypted API secret.",
            ),
        ),
        migrations.AlterField(
            model_name="affiliateprovider",
            name="api_key",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Fernet-encrypted API key.",
            ),
        ),
        migrations.AlterField(
            model_name="affiliateprovider",
            name="api_secret",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Fernet-encrypted API secret.",
            ),
        ),
    )
