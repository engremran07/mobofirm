"""Variant identity: carrier, RAM/storage on requests, unique normalised codes."""

import django.db.models.functions.text
from django.db import migrations, models

from apps.firmwares.variant_rules import normalize_variant_code


def normalise_existing_codes(apps, schema_editor):
    """Normalise stored codes and make them unique per model before the constraint.

    The first variant (lowest id) keeps a code; a later duplicate keeps its data and
    gets a ``_DUP<n>`` suffix so nothing is deleted and staff can fix it.
    """
    variant = apps.get_model("firmwares", "Variant")
    taken: set[tuple[int, str]] = set()
    for row in variant.objects.exclude(model_code="").order_by("model_id", "id"):
        code = normalize_variant_code(row.model_code)
        key = (row.model_id, code.lower())
        counter = 1
        while key in taken:
            counter += 1
            suffix = f"_DUP{counter}"
            code = (
                f"{normalize_variant_code(row.model_code)[: 64 - len(suffix)]}{suffix}"
            )
            key = (row.model_id, code.lower())
        taken.add(key)
        if code != row.model_code:
            variant.objects.filter(pk=row.pk).update(model_code=code)

    request = apps.get_model("firmwares", "VariantCreationRequest")
    seen: set[tuple[int, str]] = set()
    pending = request.objects.filter(status="pending").exclude(model_code="")
    for row in pending.order_by("model_id", "id"):
        code = normalize_variant_code(row.model_code)
        key = (row.model_id, code.lower())
        if key in seen:
            code = ""
        else:
            seen.add(key)
        if code != row.model_code:
            request.objects.filter(pk=row.pk).update(model_code=code)


class Migration(migrations.Migration):
    dependencies = (("firmwares", "0004_access_changes_and_download_rules"),)

    operations = (
        migrations.AddField(
            model_name="variant",
            name="carrier",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Carrier or operator the variant is locked to (e.g., Verizon)",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="variantcreationrequest",
            name="carrier",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="variantcreationrequest",
            name="memory",
            field=models.CharField(
                blank=True,
                default="",
                help_text="RAM and storage, e.g. 8 GB / 256 GB",
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="variantcreationrequest",
            name="name",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.RunPython(normalise_existing_codes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="variant",
            constraint=models.UniqueConstraint(
                models.F("model"),
                django.db.models.functions.text.Lower("model_code"),
                condition=models.Q(("model_code", ""), _negated=True),
                name="variant_model_code_unique_ci",
                violation_error_message=(
                    "Another variant of this model already uses this code "
                    "(codes are compared ignoring case)."
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="variantcreationrequest",
            constraint=models.UniqueConstraint(
                models.F("model"),
                django.db.models.functions.text.Lower("model_code"),
                condition=models.Q(
                    ("status", "pending"), models.Q(("model_code", ""), _negated=True)
                ),
                name="variant_request_pending_code_unique",
            ),
        ),
    )
