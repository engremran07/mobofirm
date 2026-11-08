from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("core", "0004_remove_filemanager_flag"),)

    operations = (
        migrations.AddField(
            model_name="appregistry",
            name="consent_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="appregistry",
            name="gamification_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="appregistry",
            name="bounty_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="appregistry",
            name="distribution_enabled",
            field=models.BooleanField(default=True),
        ),
    )
