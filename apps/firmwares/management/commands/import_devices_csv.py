"""Import sourced smartphone/tablet metadata from a Google Device Catalog CSV."""

from __future__ import annotations

import csv
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from apps.core.seeding import eager_background_tasks, require_local_database
from apps.firmwares.chipsets import normalize_chipset, resolve_chipset
from apps.firmwares.constants import RESERVED_BRAND_SLUGS
from apps.firmwares.models import Brand, Chipset, Model, Variant
from apps.firmwares.signal_handlers import batched_blog_sync
from apps.firmwares.variant_rules import normalize_variant_code

ALLOWED_FORM_FACTORS = {"phone", "tablet"}


def _unique_slug(
    base: str,
    identity: str,
    *,
    max_length: int,
    lookup: Callable[[str], str | None],
    reserved: bool = False,
) -> str:
    candidate = base[:max_length] or "catalog-item"
    existing_identity = lookup(candidate)
    if not reserved and (existing_identity is None or existing_identity == identity):
        return candidate

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    suffix = f"-{digest}"
    candidate = f"{base[: max_length - len(suffix)] or 'item'}{suffix}"
    counter = 2
    while True:
        existing_identity = lookup(candidate)
        if existing_identity is None or existing_identity == identity:
            return candidate
        suffix = f"-{digest}-{counter}"
        candidate = f"{base[: max_length - len(suffix)] or 'item'}{suffix}"
        counter += 1


def _ram_options(value: str) -> list[str]:
    raw = re.sub(r"\s+", "", value or "")
    values = {item for item in re.split(r"[;/,]", raw) if item}
    return sorted(values)


def _screen_size(value: str) -> str:
    return re.sub(r"\s+", "", value or "")[:64]


def _model_code(row: dict[str, str]) -> str:
    return (row.get("Device") or "").strip()[:64]


def _brand_and_model(row: dict[str, str]) -> tuple[str, str] | None:
    manufacturer = (row.get("Manufacturer") or row.get("Brand") or "").strip()
    name = (row.get("Model Name") or "").strip()
    if not manufacturer or not name:
        return None
    return manufacturer[:128], name[:128]


def _brand_slug(name: str) -> str:
    base = slugify(name)[:140] or "brand"
    return _unique_slug(
        base,
        name,
        max_length=140,
        lookup=lambda slug: (
            Brand.objects.filter(slug=slug).values_list("name", flat=True).first()
        ),
        reserved=base in RESERVED_BRAND_SLUGS,
    )


def _model_slug(brand: Brand, name: str) -> str:
    base = slugify(name)[:160] or "model"
    return _unique_slug(
        base,
        name,
        max_length=160,
        lookup=lambda slug: (
            Model.objects.filter(brand=brand, slug=slug)
            .values_list("name", flat=True)
            .first()
        ),
    )


def _variant_slug(model: Model, code: str) -> str:
    base = slugify(code)[:160] or "variant"
    normalized_code = normalize_variant_code(code)

    def existing_code(slug: str) -> str | None:
        existing = (
            Variant.objects.filter(model=model, slug=slug)
            .values_list("model_code", flat=True)
            .first()
        )
        return None if existing is None else normalize_variant_code(existing)

    return _unique_slug(
        base,
        normalized_code,
        max_length=160,
        lookup=existing_code,
    )


def _catalog_code_owners() -> dict[tuple[str, str], set[str]]:
    owners: dict[tuple[str, str], set[str]] = {}
    models = Model.objects.values_list(
        "brand__name",
        "name",
        "model_code",
        "model_codes",
        "variant_codes",
    )
    for brand_name, model_name, model_code, model_codes, variant_codes in models:
        identity = (brand_name.casefold(), model_name.casefold())
        codes = [model_code]
        for values in (model_codes, variant_codes):
            if isinstance(values, list):
                codes.extend(value for value in values if isinstance(value, str))
        for value in codes:
            code = normalize_variant_code(value)
            if code:
                owners.setdefault((identity[0], code), set()).add(identity[1])

    variants = Variant.objects.values_list(
        "model__brand__name", "model__name", "model_code"
    )
    for brand_name, model_name, model_code in variants:
        code = normalize_variant_code(model_code)
        if code:
            owners.setdefault((brand_name.casefold(), code), set()).add(
                model_name.casefold()
            )
    return owners


def _source_code_owners(source: Path) -> dict[tuple[str, str], set[str]]:
    owners: dict[tuple[str, str], set[str]] = {}
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                row.get("Form Factor") or ""
            ).strip().casefold() not in ALLOWED_FORM_FACTORS:
                continue
            metadata = _brand_and_model(row)
            code = normalize_variant_code(_model_code(row))
            if metadata is None or not code:
                continue
            manufacturer, model_name = metadata
            owners.setdefault((manufacturer.casefold(), code), set()).add(
                model_name.casefold()
            )
    return owners


class Command(BaseCommand):
    help = (
        "Import only smartphone and tablet rows from devices.csv into the firmware "
        "catalog. Dry-run by default; no firmware binaries or synthetic downloads."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("csv", type=Path, help="Path to devices.csv")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Write rows to the local test/development database.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of allowed rows after filtering.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        source = options["csv"].resolve()
        if not source.is_file():
            raise CommandError(f"CSV not found: {source}")
        if options["confirm"]:
            require_local_database(confirm=True)

        limit = int(options["limit"] or 0)
        source_code_owners = _source_code_owners(source)
        catalog_code_owners = _catalog_code_owners()
        conflicting_codes = {
            key
            for key, model_names in source_code_owners.items()
            if len(model_names) > 1
            or bool(catalog_code_owners.get(key, set()) - model_names)
        }
        counts = {
            "phone": 0,
            "tablet": 0,
            "skipped_form_factor": 0,
            "skipped_incomplete": 0,
            "skipped_ambiguous_codes": 0,
            "brands": 0,
            "models": 0,
            "variants": 0,
            "chipsets": 0,
        }

        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"Brand", "Device", "Manufacturer", "Model Name", "Form Factor"}
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise CommandError(
                    f"CSV missing required columns: {', '.join(sorted(missing))}"
                )

            confirmed = bool(options["confirm"])
            if not confirmed:
                pending_brands: set[str] = set()
                pending_models: set[tuple[str, str]] = set()
                pending_variants: set[tuple[tuple[str, str], str]] = set()
                with source.open("r", encoding="utf-8-sig", newline="") as preview:
                    preview_reader = csv.DictReader(preview)
                    chipset_slugs: set[str] = set()
                    for row in preview_reader:
                        factor = (row.get("Form Factor") or "").strip().casefold()
                        if factor not in ALLOWED_FORM_FACTORS:
                            counts["skipped_form_factor"] += 1
                            continue
                        if limit and (counts["phone"] + counts["tablet"]) >= limit:
                            break
                        metadata = _brand_and_model(row)
                        if metadata is None:
                            counts["skipped_incomplete"] += 1
                            continue
                        manufacturer, model_name = metadata
                        brand = Brand.objects.filter(name__iexact=manufacturer).first()
                        brand_key = manufacturer.casefold()
                        if brand is None and brand_key not in pending_brands:
                            pending_brands.add(brand_key)
                            counts["brands"] += 1

                        model_identity = (brand_key, model_name.casefold())
                        model = (
                            Model.objects.filter(
                                brand=brand, name__iexact=model_name
                            ).first()
                            if brand is not None
                            else None
                        )
                        if model is None and model_identity not in pending_models:
                            pending_models.add(model_identity)
                            counts["models"] += 1
                        counts[factor] += 1

                        model_code = normalize_variant_code(_model_code(row))
                        code_identity = (manufacturer.casefold(), model_code)
                        if model_code and code_identity in conflicting_codes:
                            counts["skipped_ambiguous_codes"] += 1
                        variant_identity = (model_identity, model_code)
                        variant_exists = (
                            model is not None
                            and model_code
                            and Variant.objects.filter(
                                model=model, model_code__iexact=model_code
                            ).exists()
                        )
                        if (
                            model_code
                            and code_identity not in conflicting_codes
                            and not variant_exists
                            and variant_identity not in pending_variants
                        ):
                            pending_variants.add(variant_identity)
                            counts["variants"] += 1

                        normalized = normalize_chipset(row.get("System on Chip") or "")
                        if normalized is not None:
                            chipset_slugs.add(normalized.slug)
                    counts["chipsets"] = sum(
                        not Chipset.objects.filter(slug=slug).exists()
                        for slug in chipset_slugs
                    )
                self.stdout.write(
                    "Dry-run: "
                    f"phones={counts['phone']}, tablets={counts['tablet']}, "
                    f"brands={counts['brands']}, models={counts['models']}, "
                    f"variants={counts['variants']}, chipsets={counts['chipsets']}, "
                    f"skipped_non_phone_tablet={counts['skipped_form_factor']}, "
                    f"skipped_missing_metadata={counts['skipped_incomplete']}, "
                    f"skipped_ambiguous_codes={counts['skipped_ambiguous_codes']}."
                )
                return

            with eager_background_tasks(), batched_blog_sync(), transaction.atomic():
                initial_chipset_ids = set(Chipset.objects.values_list("pk", flat=True))
                for row in reader:
                    factor = (row.get("Form Factor") or "").strip().casefold()
                    if factor not in ALLOWED_FORM_FACTORS:
                        counts["skipped_form_factor"] += 1
                        continue
                    if limit and (counts["phone"] + counts["tablet"]) >= limit:
                        break

                    metadata = _brand_and_model(row)
                    if metadata is None:
                        counts["skipped_incomplete"] += 1
                        continue
                    manufacturer, model_name = metadata
                    model_code = _model_code(row)
                    code_identity = (
                        manufacturer.casefold(),
                        normalize_variant_code(model_code),
                    )
                    code_is_ambiguous = (
                        bool(model_code) and code_identity in conflicting_codes
                    )
                    counts["skipped_ambiguous_codes"] += int(code_is_ambiguous)
                    brand = Brand.objects.filter(name__iexact=manufacturer).first()
                    if brand is None:
                        counts["brands"] += 1
                        brand = Brand.objects.create(
                            name=manufacturer,
                            slug=_brand_slug(manufacturer),
                        )

                    chipset_text = (row.get("System on Chip") or "").strip()[:256]
                    chipset = resolve_chipset(chipset_text, create=True)

                    model = Model.objects.filter(
                        brand=brand, name__iexact=model_name
                    ).first()
                    if model is None:
                        model = Model.objects.create(
                            brand=brand,
                            slug=_model_slug(brand, model_name),
                            name=model_name,
                            marketing_name=model_name,
                            model_code="" if code_is_ambiguous else model_code,
                            chipset=chipset_text,
                            chipset_ref=chipset,
                            gpu=(row.get("GPU") or "")[:256],
                            ram_options=_ram_options(row.get("RAM (TotalMem)") or ""),
                            display_size=_screen_size(row.get("Screen Sizes") or ""),
                        )
                        counts["models"] += 1

                    normalized_code = normalize_variant_code(model_code)
                    if (
                        normalized_code
                        and not code_is_ambiguous
                        and not Variant.objects.filter(
                            model=model, model_code__iexact=normalized_code
                        ).exists()
                    ):
                        Variant.objects.create(
                            model=model,
                            slug=_variant_slug(model, normalized_code),
                            name="Listed",
                            model_code=normalized_code,
                            chipset=chipset_text[:128],
                            chipset_ref=chipset,
                            ram_options=_ram_options(row.get("RAM (TotalMem)") or ""),
                        )
                        counts["variants"] += 1

                    counts[factor] += 1

                counts["chipsets"] = Chipset.objects.exclude(
                    pk__in=initial_chipset_ids
                ).count()

        self.stdout.write(
            "Imported smartphone/tablet catalog metadata: "
            f"phones={counts['phone']}, tablets={counts['tablet']}, "
            f"brands={counts['brands']}, models={counts['models']}, "
            f"variants={counts['variants']}, chipsets={counts['chipsets']}, "
            f"skipped_non_phone_tablet={counts['skipped_form_factor']}, "
            f"skipped_missing_metadata={counts['skipped_incomplete']}, "
            f"skipped_ambiguous_codes={counts['skipped_ambiguous_codes']}."
        )
