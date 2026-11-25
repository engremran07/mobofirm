from typing import Any, ClassVar

import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify

_CANONICAL_NAMES = {
    "mediatek": "MediaTek",
    "mtk": "MediaTek",
    "qualcomm": "Qualcomm",
    "snapdragon": "Qualcomm",
    "samsung": "Samsung",
    "exynos": "Samsung",
    "unisoc": "Unisoc",
    "spreadtrum": "Unisoc",
    "sprd": "Unisoc",
    "rda": "Unisoc",
    "rda microelectronics": "Unisoc",
    "hisilicon": "HiSilicon",
    "kirin": "HiSilicon",
    "google": "Google",
    "tensor": "Google",
    "rockchip": "Rockchip",
    "allwinner": "Allwinner",
    "apple": "Apple",
}


def _vendor_name(raw_name):
    cleaned = " ".join((raw_name or "").split())
    return _CANONICAL_NAMES.get(cleaned.casefold(), cleaned[:64])


def _unique_slug(Vendor, name, using):
    base = slugify(name)[:72] or "chipset-vendor"
    candidate = base
    suffix = 2
    while Vendor.objects.using(using).filter(slug=candidate).exists():
        candidate = f"{base[: 79 - len(str(suffix))]}-{suffix}"
        suffix += 1
    return candidate


def normalize_vendors(apps, schema_editor):
    Chipset = apps.get_model("firmwares", "Chipset")
    Vendor = apps.get_model("firmwares", "ChipsetVendor")
    using = schema_editor.connection.alias
    rows = list(
        Chipset.objects.using(using)
        .order_by("pk")
        .values_list("pk", "legacy_vendor", "name")
    )
    targets: dict[tuple[str, str], int] = {}
    for pk, raw_vendor, name in rows:
        vendor_name = _vendor_name(raw_vendor)
        if not vendor_name:
            raise RuntimeError(
                f"Chipset #{pk} has no vendor; normalize it before this migration."
            )
        key = (vendor_name, name)
        if key in targets:
            raise RuntimeError(
                "Chipset vendor normalization would merge chipset rows "
                f"#{targets[key]} and #{pk} ({vendor_name} {name}); resolve the "
                "duplicate explicitly before migrating."
            )
        targets[key] = pk

    vendors: dict[str, Any] = {}
    aliases: dict[str, set[str]] = {}
    for _pk, raw_vendor, _name in rows:
        cleaned = " ".join((raw_vendor or "").split())[:64]
        name = _vendor_name(cleaned)
        if name in vendors:
            if cleaned and cleaned.casefold() != name.casefold():
                aliases.setdefault(name, set()).add(cleaned)
            continue
        vendor = Vendor.objects.using(using).filter(name=name).first()
        if vendor is None:
            vendor = Vendor.objects.using(using).create(
                name=name,
                slug=_unique_slug(Vendor, name, using),
                aliases=[],
                os_family="ios" if name == "Apple" else "android",
            )
        vendors[name] = vendor
        if cleaned and cleaned.casefold() != name.casefold():
            aliases.setdefault(name, set()).add(cleaned)

    chipset_updates = [
        Chipset(pk=chipset_id, vendor_id=vendors[_vendor_name(raw_vendor)].pk)
        for chipset_id, raw_vendor, _name in rows
    ]
    Chipset.objects.using(using).bulk_update(
        chipset_updates, ["vendor"], batch_size=1000
    )

    for name, additions in aliases.items():
        vendor = vendors[name]
        vendor.aliases = sorted(set(vendor.aliases or ()) | additions)
        vendor.save(using=using, update_fields=["aliases"])


def restore_vendor_labels(apps, schema_editor):
    Chipset = apps.get_model("firmwares", "Chipset")
    using = schema_editor.connection.alias
    updates = []
    for chipset in Chipset.objects.using(using).select_related("vendor").iterator():
        chipset.legacy_vendor = chipset.vendor.name
        updates.append(chipset)
    Chipset.objects.using(using).bulk_update(
        updates, ["legacy_vendor"], batch_size=1000
    )


class Migration(migrations.Migration):
    dependencies: ClassVar = [
        ("firmwares", "0012_remove_ai_field_naming"),
    ]

    operations: ClassVar = [
        migrations.CreateModel(
            name="ChipsetVendor",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=64, unique=True)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("aliases", models.JSONField(blank=True, default=list)),
                (
                    "os_family",
                    models.CharField(blank=True, default="android", max_length=32),
                ),
                (
                    "default_flash_mode",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("logo_url", models.URLField(blank=True, default="", max_length=500)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RemoveConstraint(
            model_name="chipset",
            name="chipset_vendor_name_unique",
        ),
        migrations.RemoveIndex(
            model_name="chipset",
            name="chipset_vendor_idx",
        ),
        migrations.RenameField(
            model_name="chipset",
            old_name="vendor",
            new_name="legacy_vendor",
        ),
        migrations.AlterField(
            model_name="chipset",
            name="legacy_vendor",
            field=models.CharField(
                blank=True,
                help_text="e.g. MediaTek, Qualcomm",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="chipset",
            name="vendor",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="chipsets",
                to="firmwares.chipsetvendor",
            ),
        ),
        migrations.RunPython(normalize_vendors, restore_vendor_labels),
        migrations.AlterField(
            model_name="chipset",
            name="vendor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="chipsets",
                to="firmwares.chipsetvendor",
            ),
        ),
        migrations.RemoveField(
            model_name="chipset",
            name="legacy_vendor",
        ),
        migrations.AlterModelOptions(
            name="chipset",
            options={
                "ordering": ["vendor__name", "name"],
                "verbose_name": "Chipset",
                "verbose_name_plural": "Chipsets",
            },
        ),
        migrations.AddConstraint(
            model_name="chipset",
            constraint=models.UniqueConstraint(
                fields=("vendor", "name"),
                name="chipset_vendor_name_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="chipset",
            index=models.Index(
                fields=["vendor", "family"],
                name="chipset_vendor_idx",
            ),
        ),
    ]
