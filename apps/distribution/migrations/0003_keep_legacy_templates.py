from django.db import migrations


def keep_legacy_copy(apps, _schema_editor):
    """Channels that existed before the composer keep posting with their templates."""
    channel = apps.get_model("distribution", "Channel")
    channel.objects.update(use_composer=False)


class Migration(migrations.Migration):
    dependencies = (("distribution", "0002_distribution_engine"),)

    operations = (migrations.RunPython(keep_legacy_copy, migrations.RunPython.noop),)
