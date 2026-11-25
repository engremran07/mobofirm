from __future__ import annotations

import csv
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.firmwares.management.commands.import_devices_csv import (
    ALLOWED_FORM_FACTORS,
)
from apps.firmwares.models import Brand, Model, Variant

pytestmark = pytest.mark.django_db


def _csv(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    if rows is None:
        rows = [
            {
                "Brand": "testbrand",
                "Device": "TEST-1",
                "Manufacturer": "Test Manufacturer",
                "Model Name": "Test Phone",
                "RAM (TotalMem)": "6000MB",
                "Form Factor": "Phone",
                "System on Chip": "Qualcomm Snapdragon 8 Gen 3",
                "GPU": "Adreno 750",
                "Screen Sizes": "1080x2400",
            },
            {
                "Brand": "testbrand",
                "Device": "TEST-TV",
                "Manufacturer": "Test Manufacturer",
                "Model Name": "Test TV",
                "RAM (TotalMem)": "2000MB",
                "Form Factor": "TV",
                "System on Chip": "Mediatek MT8183",
                "GPU": "Mali",
                "Screen Sizes": "1080x1920",
            },
            {
                "Brand": "testbrand",
                "Device": "TEST-2",
                "Manufacturer": "Test Manufacturer",
                "Model Name": "",
                "RAM (TotalMem)": "6000MB",
                "Form Factor": "Tablet",
                "System on Chip": "Qualcomm Snapdragon 8 Gen 3",
                "GPU": "Adreno 750",
                "Screen Sizes": "1080x2400",
            },
        ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Brand",
                "Device",
                "Manufacturer",
                "Model Name",
                "RAM (TotalMem)",
                "Form Factor",
                "System on Chip",
                "GPU",
                "Screen Sizes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_the_importer_has_noop_safety_by_default(tmp_path: Path, capsys) -> None:
    path = tmp_path / "devices.csv"
    _csv(path)
    call_command("import_devices_csv", str(path))

    output = capsys.readouterr().out
    assert "Dry-run:" in output
    assert "phones=1, tablets=0" in output
    assert "skipped_non_phone_tablet=1" in output
    assert "skipped_missing_metadata=1" in output
    assert "skipped_ambiguous_codes=0" in output
    assert Brand.objects.count() == 0


def test_confirmed_import_is_idempotent_and_reports_incomplete_rows(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "devices.csv"
    _csv(path)

    call_command("import_devices_csv", str(path), "--confirm")
    first_output = capsys.readouterr().out
    assert "phones=1, tablets=0" in first_output
    assert "skipped_missing_metadata=1" in first_output
    assert "skipped_ambiguous_codes=0" in first_output
    assert Brand.objects.count() == 1
    assert Model.objects.count() == 1
    assert Variant.objects.count() == 1

    call_command("import_devices_csv", str(path), "--confirm")
    second_output = capsys.readouterr().out
    assert "brands=0, models=0, variants=0" in second_output
    assert Brand.objects.count() == 1
    assert Model.objects.count() == 1
    assert Variant.objects.count() == 1


def test_confirmed_import_avoids_brand_model_and_variant_slug_collisions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "devices.csv"
    common = {
        "RAM (TotalMem)": "6000MB",
        "Form Factor": "Phone",
        "System on Chip": "",
        "GPU": "",
        "Screen Sizes": "",
    }
    _csv(
        path,
        [
            {
                **common,
                "Brand": "Acme + Co",
                "Manufacturer": "Acme + Co",
                "Model Name": "Tab A9 Pro+",
                "Device": "K31",
            },
            {
                **common,
                "Brand": "Acme + Co",
                "Manufacturer": "Acme + Co",
                "Model Name": "Tab A9 Pro+",
                "Device": "K31_",
            },
            {
                **common,
                "Brand": "Acme Co",
                "Manufacturer": "Acme Co",
                "Model Name": "Tab A9 Pro",
                "Device": "K31",
            },
        ],
    )

    call_command("import_devices_csv", str(path), "--confirm")

    assert Brand.objects.count() == 2
    assert Brand.objects.values_list("slug", flat=True).distinct().count() == 2
    assert Model.objects.count() == 2
    assert Model.objects.values_list("brand_id", "slug").distinct().count() == 2
    assert Variant.objects.count() == 3
    assert Variant.objects.values_list("model_id", "slug").distinct().count() == 3


def test_confirmed_import_skips_codes_shared_by_different_models(
    tmp_path: Path, capsys
) -> None:
    path = tmp_path / "devices.csv"
    common = {
        "RAM (TotalMem)": "6000MB",
        "Form Factor": "Phone",
        "System on Chip": "",
        "GPU": "",
        "Screen Sizes": "",
        "Brand": "Acme",
        "Manufacturer": "Acme",
        "Device": "SAME-CODE",
    }
    _csv(
        path,
        [
            {**common, "Model Name": "Model One"},
            {**common, "Model Name": "Model Two"},
        ],
    )

    call_command("import_devices_csv", str(path), "--confirm")

    assert Model.objects.count() == 2
    assert not Model.objects.exclude(model_code="").exists()
    assert Variant.objects.count() == 0
    assert "skipped_ambiguous_codes=2" in capsys.readouterr().out


def test_the_importer_only_accepts_phone_and_tablet_form_factors() -> None:
    assert "phone" in ALLOWED_FORM_FACTORS
    assert "tablet" in ALLOWED_FORM_FACTORS
