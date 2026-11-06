from typing import ClassVar

from django.db import migrations


def clear_download_tokens(apps, schema_editor):
    """Tokens are short-lived; drop them before re-keying to generic firmware refs."""
    apps.get_model("firmwares", "DownloadToken").objects.all().delete()


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("firmwares", "0017_prebuilt_flashing_tool_categories"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(clear_download_tokens, migrations.RunPython.noop),
    ]
