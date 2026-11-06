from typing import ClassVar

from django.db import migrations

from apps.firmwares.chipsets import normalize_chipset


def backfill_chipsets(apps, schema_editor):
    """Resolve existing free-text chipsets to normalised Chipset rows."""
    Chipset = apps.get_model("firmwares", "Chipset")
    cache = {}

    def resolve(raw):
        normalized = normalize_chipset(raw)
        if normalized is None:
            return None
        if normalized.slug not in cache:
            cache[normalized.slug], _ = Chipset.objects.get_or_create(
                slug=normalized.slug,
                defaults={
                    "vendor": normalized.vendor,
                    "family": normalized.family,
                    "name": normalized.name,
                    "aliases": [],
                },
            )
        return cache[normalized.slug]

    for model_name in ("Model", "Variant"):
        target = apps.get_model("firmwares", model_name)
        for row in target.objects.filter(chipset_ref__isnull=True).exclude(chipset=""):
            chipset = resolve(row.chipset)
            if chipset is not None:
                target.objects.filter(pk=row.pk).update(chipset_ref=chipset)


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("firmwares", "0019_chipsets_external_sources_generic_tokens"),
    ]

    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(backfill_chipsets, migrations.RunPython.noop),
    ]
