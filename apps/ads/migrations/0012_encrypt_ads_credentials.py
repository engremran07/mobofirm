"""Encrypt any legacy plaintext credentials in the Ads app.

This migration is intentionally irreversible: rolling back to plaintext storage
would undo the security fix.
"""

from cryptography.fernet import InvalidToken
from django.db import migrations, models


def encrypt_existing_secrets(apps, schema_editor):
    from apps.firmwares.crypto import decrypt_password, encrypt_password

    database = schema_editor.connection.alias
    credentials = (
        ("AdsSettings", ("amazon_paapi_secret_key",)),
        ("AdNetwork", ("api_key", "api_secret")),
        ("AffiliateProvider", ("api_key", "api_secret")),
    )
    for model_name, fields in credentials:
        model = apps.get_model("ads", model_name)
        queryset = model.objects.using(database)
        for record in queryset.iterator():
            changed_fields = []
            for field in fields:
                value = getattr(record, field)
                if not value:
                    continue
                try:
                    decrypt_password(value)
                except InvalidToken:
                    setattr(record, field, encrypt_password(value))
                    changed_fields.append(field)
            if changed_fields:
                record.save(using=database, update_fields=changed_fields)


class Migration(migrations.Migration):
    dependencies = (("ads", "0011_ad_unlocks"),)

    operations = (
        migrations.AlterField(
            model_name="adssettings",
            name="amazon_paapi_secret_key",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Fernet-encrypted Amazon PA-API secret key.",
            ),
        ),
        migrations.RunPython(encrypt_existing_secrets),
    )
