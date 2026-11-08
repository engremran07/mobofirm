from typing import ClassVar

from django.db import migrations

CATEGORIES = (
    {
        "name": "Android platform tools",
        "slug": "android-platform-tools",
        "description": (
            "Official Android developer tools and supported device workflows."
        ),
        "icon": "smartphone",
        "sort_order": 1,
    },
    {
        "name": "OEM recovery utilities",
        "slug": "oem-recovery-utilities",
        "description": (
            "Manufacturer-provided recovery utilities for supported devices."
        ),
        "icon": "shield-check",
        "sort_order": 2,
    },
)


def create_default_categories(apps, schema_editor):
    category_model = apps.get_model("firmwares", "FlashingToolCategory")
    categories = category_model.objects.using(schema_editor.connection.alias)

    for data in CATEGORIES:
        if categories.filter(slug=data["slug"]).exists():
            continue
        if categories.filter(name=data["name"]).exists():
            continue
        categories.create(**data)


def remove_unused_default_categories(apps, schema_editor):
    category_model = apps.get_model("firmwares", "FlashingToolCategory")
    categories = category_model.objects.using(schema_editor.connection.alias)

    for data in CATEGORIES:
        categories.filter(
            slug=data["slug"],
            name=data["name"],
            tools__isnull=True,
        ).delete()


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("firmwares", "0002_blog_and_device_links"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(
            create_default_categories,
            reverse_code=remove_unused_default_categories,
        ),
    ]
