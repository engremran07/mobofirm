"""Idempotent device-catalogue seeding through the real firmware services.

Brands, models and variants are created with the ORM (their post-save automation
creates the brand, model and chipset articles). Firmware is never written directly:
each file is submitted as an external listing and approved by the reviewer account
through ``approve_pending_firmware``, so review fields, tags and the automated
articles are produced by the same code that runs in production.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.management.base import CommandError
from django.db.models import Q
from django.utils.text import slugify

from apps.core.seeding import SeedReport

from .chipsets import normalize_chipset, resolve_chipset
from .guide_service import generate_guide_for_brand_model
from .models import (
    Brand,
    Chipset,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
    OfficialFirmware,
    Variant,
)
from .seed_data import (
    BRANDS,
    CHIPSETS,
    CHIPSETS_BY_KEY,
    GUIDES,
    TOOL_CATEGORIES,
    TOOLS,
)
from .seed_data.types import (
    BrandSeed,
    ChipsetSeed,
    FirmwarePlan,
    ModelSeed,
    VariantSeed,
    build_number,
    release_dates,
)
from .services import approve_pending_firmware, submit_external_firmware
from .sources import allowed_external_hosts

EXTERNAL_HOST = "downloads.example.invalid"
OFFICIAL_CATEGORY = "official"
_UNSAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9._+-]+")

GUIDE_BODIES: dict[str, str] = {
    FlashingGuideTemplate.GuideType.STOCK_FLASH: """{intro}

## Before you start

- Find the exact model code of your {brand} {model} and match it to the package.
- Back up your data: flashing usually erases the phone.
- Charge the battery above 50 percent and use a good data cable.
- Verify the package checksum against a value published by the manufacturer.

## Tools

{tools}

## Steps

{steps}

## If something goes wrong

Do not repeat a failed flash blindly. Re-check the model code, the package region
and the USB connection, then ask the manufacturer or an authorised service centre
if the phone still does not start.
""",
    FlashingGuideTemplate.GuideType.UNLOCK_BOOTLOADER: """{intro}

## Before you start

- Unlocking erases all data, so back up first.
- Check that your carrier or region allows unlocking for the {brand} {model}.
- Some apps that rely on device integrity checks stop working on an unlocked phone.

## Tools

{tools}

## Steps

{steps}

## Afterwards

Keep a copy of the official firmware for your model code so you can return to the
stock software and lock the bootloader again if you need to.
""",
}


def model_slug(name: str) -> str:
    return slugify(name.replace("+", " plus "))


def catalogue_name(brand: BrandSeed, seed: ModelSeed) -> str:
    """The model name without a repeated brand prefix (``Honor 90`` -> ``90``)."""
    return seed.name.removeprefix(f"{brand.name} ")


def marketing_name(brand: BrandSeed, seed: ModelSeed) -> str:
    return seed.marketing_name or f"{brand.name} {catalogue_name(brand, seed)}"


def chipset_slug(key: str) -> str:
    normalized = normalize_chipset(CHIPSETS_BY_KEY[key].display_name)
    if normalized is None:
        raise CommandError(f"Chipset key {key!r} cannot be normalised.")
    return normalized.slug


def chipset_text(key: str) -> str:
    return CHIPSETS_BY_KEY[key].display_name if key else ""


def os_text(seed: ModelSeed) -> str:
    if seed.os_label:
        return f"{seed.os_label} (Android {seed.android})"
    return f"Android {seed.android}"


def variant_seeds(seed: ModelSeed) -> tuple[VariantSeed, ...]:
    if seed.variants:
        return seed.variants
    return (VariantSeed("Global", "Global", seed.model_code),)


def model_description(brand: BrandSeed, seed: ModelSeed) -> str:
    marketing = marketing_name(brand, seed)
    code = f" (model code {seed.model_code})" if seed.model_code else ""
    sentences = [
        (
            f"{marketing}{code} was released in {seed.released:%B %Y} "
            f"running {os_text(seed)}."
        )
    ]
    if seed.chipset:
        sentences.append(f"It is powered by the {chipset_text(seed.chipset)}.")
    sentences.append(
        "This page lists its variants, approved firmware downloads and the "
        "flashing tools that support it."
    )
    return " ".join(sentences)


def select_brands(limit: int | None = None) -> tuple[BrandSeed, ...]:
    if limit is None:
        return BRANDS
    if limit < 1:
        raise CommandError("--limit-brands must be at least 1.")
    return BRANDS[:limit]


def seed_chipsets(
    seeds: Sequence[ChipsetSeed] = CHIPSETS,
) -> tuple[SeedReport, dict[str, Chipset]]:
    report = SeedReport()
    chipsets: dict[str, Chipset] = {}
    for seed in seeds:
        normalized = normalize_chipset(seed.display_name)
        if normalized is None:
            raise CommandError(f"Chipset {seed.display_name!r} cannot be normalised.")
        existed = Chipset.objects.filter(slug=normalized.slug).exists()
        chipset = resolve_chipset(seed.display_name)
        if chipset is None or (chipset.vendor, chipset.name) != (
            seed.vendor,
            seed.name,
        ):
            raise CommandError(
                f"Chipset {seed.display_name!r} resolved to an unexpected record."
            )
        aliases = [*chipset.aliases]
        aliases += [alias for alias in seed.aliases if alias not in aliases]
        changed = {
            "family": seed.family or chipset.family,
            "description": chipset.description or seed.description,
            "aliases": aliases,
        }
        if any(getattr(chipset, field) != value for field, value in changed.items()):
            for field, value in changed.items():
                setattr(chipset, field, value)
            chipset.save()
        chipsets[seed.key] = chipset
        report.record("chipsets", created=not existed)
    return report, chipsets


def _find_brand(seed: BrandSeed) -> Brand | None:
    return Brand.objects.filter(Q(slug=seed.slug) | Q(name=seed.name)).first()


def seed_brands_and_models(brands: Sequence[BrandSeed]) -> SeedReport:
    """Create brands, models and variants; existing records are left untouched."""
    report = SeedReport()
    for seed in brands:
        brand = _find_brand(seed)
        if brand is None:
            brand = Brand.objects.create(
                name=seed.name,
                slug=seed.slug,
                website_url=seed.website_url,
                description=seed.description,
                is_featured=seed.featured,
            )
            report.record("brands", created=True)
        else:
            report.record("brands", created=False)
        for model_seed in seed.models:
            _seed_model(brand, seed, model_seed, report)
    return report


def _seed_model(
    brand: Brand, brand_seed: BrandSeed, seed: ModelSeed, report: SeedReport
) -> None:
    variants = variant_seeds(seed)
    codes = [seed.model_code, *(item.model_code for item in variants)]
    model_codes = list(dict.fromkeys(code for code in codes if code))
    regions = list(dict.fromkeys(item.region for item in variants if item.region))
    model, created = Model.objects.get_or_create(
        brand=brand,
        slug=model_slug(catalogue_name(brand_seed, seed)),
        defaults={
            "name": catalogue_name(brand_seed, seed),
            "marketing_name": marketing_name(brand_seed, seed),
            "codename": seed.codename,
            "model_code": seed.model_code,
            "model_codes": model_codes,
            "description": model_description(brand_seed, seed),
            "chipset": chipset_text(seed.chipset),
            "os_version": os_text(seed),
            "release_date": seed.released,
            "status": "available",
            "regions": regions,
            "is_active": True,
        },
    )
    report.record("models", created=created)
    for variant_seed in variants:
        _, variant_created = Variant.objects.get_or_create(
            model=model,
            slug=slugify(variant_seed.name),
            defaults={
                "name": variant_seed.name,
                "region": variant_seed.region,
                "model_code": variant_seed.model_code,
                "chipset": chipset_text(variant_seed.chipset or seed.chipset),
            },
        )
        report.record("variants", created=variant_created)


def firmware_file_name(
    brand: BrandSeed, seed: ModelSeed, variant: VariantSeed, number: str, ext: str
) -> str:
    stem = "-".join(
        (
            brand.slug,
            model_slug(catalogue_name(brand, seed)),
            slugify(variant.name),
            _UNSAFE_FILE_CHARS.sub("-", number).strip("-"),
        )
    )
    return f"{stem}{ext}"


def firmware_plans(
    brand: BrandSeed, seed: ModelSeed
) -> tuple[FirmwarePlan, FirmwarePlan]:
    """The launch build and a later update of a model's first variant."""
    variant = variant_seeds(seed)[0]
    coded = replace(seed, model_code=variant.model_code or seed.model_code)
    dates = release_dates(seed.released)
    plans: list[FirmwarePlan] = []
    for seq, (build_date, patch) in enumerate(dates, start=1):
        number = build_number(brand.scheme, coded, seq, build_date)
        file_name = firmware_file_name(
            brand, seed, variant, number, brand.scheme.extensions[seq - 1]
        )
        plans.append(
            FirmwarePlan(
                build_number=number,
                android_version=str(seed.android),
                security_patch=patch,
                build_date=build_date,
                package_format=brand.scheme.package_formats[seq - 1],
                file_name=file_name,
                url=(
                    f"https://{EXTERNAL_HOST}/{brand.slug}/"
                    f"{model_slug(catalogue_name(brand, seed))}/{file_name}"
                ),
            )
        )
    return plans[0], plans[1]


def seed_firmware(
    brands: Sequence[BrandSeed],
    *,
    uploader: AbstractBaseUser,
    reviewer: AbstractBaseUser,
) -> SeedReport:
    """Submit every planned external listing and approve it as official firmware."""
    if EXTERNAL_HOST not in allowed_external_hosts():
        raise CommandError(
            f"{EXTERNAL_HOST} must be listed in FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS."
        )
    report = SeedReport()
    for brand_seed in brands:
        brand = Brand.objects.get(slug=brand_seed.slug)
        for seed in brand_seed.models:
            model = Model.objects.get(
                brand=brand, slug=model_slug(catalogue_name(brand_seed, seed))
            )
            variant = Variant.objects.get(
                model=model, slug=slugify(variant_seeds(seed)[0].name)
            )
            device = marketing_name(brand_seed, seed)
            for plan in firmware_plans(brand_seed, seed):
                if OfficialFirmware.objects.filter(
                    variant=variant,
                    original_file_name=plan.file_name,
                    build_number=plan.build_number,
                ).exists():
                    report.record("firmware", created=False)
                    continue
                pending = submit_external_firmware(
                    uploader=uploader,
                    url=plan.url,
                    original_file_name=plan.file_name,
                    uploaded_brand=brand,
                    uploaded_model=model,
                    uploaded_variant=variant,
                    package_format=plan.package_format,
                    extra_info=f"External download reference for {device}.",
                )
                approve_pending_firmware(
                    pending,
                    reviewer,
                    category=OFFICIAL_CATEGORY,
                    notes=f"Approved as official firmware listing for {device}.",
                    package_format=plan.package_format,
                    chipset=chipset_text(variant_chipset_key(seed)),
                    build_number=plan.build_number,
                    android_version=plan.android_version,
                    security_patch=plan.security_patch,
                    build_date=plan.build_date,
                )
                report.record("firmware", created=True)
    return report


def variant_chipset_key(seed: ModelSeed) -> str:
    return variant_seeds(seed)[0].chipset or seed.chipset


def seed_tools() -> SeedReport:
    """Create tool categories, tools and their brand and chipset links."""
    report = SeedReport()
    categories: dict[str, FlashingToolCategory] = {}
    for category_seed in TOOL_CATEGORIES:
        category = FlashingToolCategory.objects.filter(
            Q(slug=category_seed.slug) | Q(name=category_seed.name)
        ).first()
        created = category is None
        if category is None:
            category = FlashingToolCategory.objects.create(
                slug=category_seed.slug,
                name=category_seed.name,
                description=category_seed.description,
                icon=category_seed.icon,
                sort_order=category_seed.sort_order,
            )
        categories[category_seed.slug] = category
        report.record("tool categories", created=created)

    chipsets = {chipset.slug: chipset for chipset in Chipset.objects.all()}
    brands = {brand.slug: brand for brand in Brand.objects.all()}
    for tool_seed in TOOLS:
        tool, created = FlashingTool.objects.get_or_create(
            slug=tool_seed.slug,
            defaults={
                "category": categories[tool_seed.category],
                "name": tool_seed.name,
                "description": tool_seed.description,
                "tool_type": tool_seed.tool_type,
                "platform": tool_seed.platform,
                "risk_level": tool_seed.risk_level,
                "official_url": tool_seed.official_url,
                "download_url": tool_seed.download_url,
                "is_free": True,
                "is_featured": tool_seed.featured,
                "instructions": tool_seed.instructions,
                "features": list(tool_seed.features),
                "requirements": list(tool_seed.requirements),
            },
        )
        tool.supported_brands.add(
            *(brands[slug] for slug in tool_seed.brands if slug in brands)
        )
        linked = [
            chipsets[normalized.slug]
            for key in tool_seed.chipsets
            if (normalized := normalize_chipset(CHIPSETS_BY_KEY[key].display_name))
            and normalized.slug in chipsets
        ]
        tool.chipsets.add(*linked)
        report.record("tools", created=created)
    return report


def seed_guides(*, publisher: AbstractBaseUser) -> SeedReport:
    """Generate flashing guides through the guide service and publish them.

    The service creates each guide in review under the automation author; the
    publisher account then publishes it through the blog workflow.
    """
    from apps.blog.models import PostStatus
    from apps.blog.services import workflow

    report = SeedReport()
    tools = {tool.slug: tool for tool in FlashingTool.objects.all()}
    for guide_seed in GUIDES:
        brand = Brand.objects.filter(slug=guide_seed.brand).first()
        if brand is None:
            continue
        model = None
        if guide_seed.model_name:
            model = Model.objects.filter(
                brand=brand, slug=model_slug(guide_seed.model_name)
            ).first()
            if model is None:
                continue
        template, _ = FlashingGuideTemplate.objects.get_or_create(
            title_template=guide_seed.title,
            guide_type=guide_seed.guide_type,
            brand=brand,
            chipset_filter=guide_seed.chipset_filter,
            defaults={
                "body_template": GUIDE_BODIES[guide_seed.guide_type].replace(
                    "{intro}", guide_seed.intro
                ),
                "summary_template": (
                    "A practical {guide_type} guide for {brand} {model}, with the "
                    "tools and steps involved."
                ),
                "auto_generate": False,
                "submit_for_review": True,
                "is_active": True,
            },
        )
        template.recommended_tools.add(
            *(tools[slug] for slug in guide_seed.tools if slug in tools)
        )
        guide = generate_guide_for_brand_model(template, brand, model)
        created = guide is not None
        if guide is None:
            guide = GeneratedFlashingGuide.objects.get(
                template=template, brand=brand, model=model
            )
        report.record("guides", created=created)
        if guide.post.status == PostStatus.IN_REVIEW:
            workflow.publish(guide.post, user=publisher)
            report.record("guides published", created=True)
    return report


def seed_catalog(
    brands: Sequence[BrandSeed], *, uploader, reviewer, publisher
) -> SeedReport:
    """Run every catalogue step in dependency order."""
    total = SeedReport()
    used = {
        key
        for brand in brands
        for seed in brand.models
        for key in (seed.chipset, *(variant.chipset for variant in seed.variants))
        if key
    }
    chipset_report, _ = seed_chipsets(
        [chipset for chipset in CHIPSETS if chipset.key in used]
    )
    total.merge(chipset_report)
    total.merge(seed_brands_and_models(brands))
    total.merge(seed_firmware(brands, uploader=uploader, reviewer=reviewer))
    total.merge(seed_tools())
    total.merge(seed_guides(publisher=publisher))
    return total


def past_dates_only(plans: Sequence[FirmwarePlan], today: date) -> bool:
    return all(
        plan.build_date <= today and plan.security_patch <= today for plan in plans
    )
