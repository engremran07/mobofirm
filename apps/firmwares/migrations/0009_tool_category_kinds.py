"""Tool and guide categories: kind, parent and source references.

Additive only: new nullable or defaulted columns plus partial unique constraints that
hold for the empty set of generated categories, so existing rows stay valid as
``general`` categories and the migration reverses cleanly.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("firmwares", "0008_download_link_shortener"),)

    operations = (
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="brand",
            field=models.ForeignKey(
                blank=True,
                help_text="The brand this category was generated for (brand categories).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tool_categories",
                to="firmwares.brand",
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="family",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Chipset family label (family categories).",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="is_auto",
            field=models.BooleanField(
                default=False,
                help_text="Generated and kept in sync automatically from the catalogue.",
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="kind",
            field=models.CharField(
                choices=[
                    ("general", "General"),
                    ("chipset_vendor", "Chipset vendor"),
                    ("chipset_family", "Chipset family"),
                    ("brand", "Brand"),
                    ("os_type", "Operating system"),
                ],
                db_index=True,
                default="general",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="os_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Operating system key, e.g. android or ios (OS categories).",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                help_text="Chipset families sit under their vendor.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="children",
                to="firmwares.flashingtoolcategory",
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="slug_history",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Earlier slugs; they redirect to the current one.",
            ),
        ),
        migrations.AddField(
            model_name="flashingtoolcategory",
            name="vendor",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Canonical chipset vendor label (vendor and family categories).",
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="flashingtoolcategory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "chipset_vendor")),
                fields=("vendor",),
                name="tool_cat_one_per_vendor",
            ),
        ),
        migrations.AddConstraint(
            model_name="flashingtoolcategory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "chipset_family")),
                fields=("vendor", "family"),
                name="tool_cat_one_per_family",
            ),
        ),
        migrations.AddConstraint(
            model_name="flashingtoolcategory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("brand__isnull", False), ("kind", "brand")),
                fields=("brand",),
                name="tool_cat_one_per_brand",
            ),
        ),
        migrations.AddConstraint(
            model_name="flashingtoolcategory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("kind", "os_type")),
                fields=("os_key",),
                name="tool_cat_one_per_os",
            ),
        ),
    )
