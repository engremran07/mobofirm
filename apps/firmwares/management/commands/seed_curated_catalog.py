"""Load a small, sourced device and tool catalog for local UI validation."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.blog.models import Category, Post
from apps.blog.services import PostService
from apps.firmwares.models import (
    Brand,
    FlashingTool,
    FlashingToolCategory,
    Model,
    Variant,
)

BRANDS = (
    {
        "name": "Samsung",
        "slug": "samsung",
        "website_url": "https://www.samsung.com/",
        "description": "Samsung Galaxy device catalog.",
        "models": (
            {
                "name": "Galaxy S24 Ultra",
                "slug": "galaxy-s24-ultra",
                "marketing_name": "Samsung Galaxy S24 Ultra",
                "model_code": "SM-S928B",
                "model_codes": ["SM-S928B", "SM-S928U1"],
                "regions": ["Global", "United States"],
                "variants": (
                    {
                        "name": "Global",
                        "slug": "global",
                        "region": "Global",
                        "model_code": "SM-S928B",
                    },
                    {
                        "name": "United States unlocked",
                        "slug": "us-unlocked",
                        "region": "United States",
                        "model_code": "SM-S928U1",
                    },
                ),
            },
        ),
    },
    {
        "name": "Xiaomi",
        "slug": "xiaomi",
        "website_url": "https://www.mi.com/global/",
        "description": "Xiaomi, Redmi, and POCO device catalog.",
        "models": (
            {
                "name": "Xiaomi 14",
                "slug": "xiaomi-14",
                "marketing_name": "Xiaomi 14",
                "model_code": "23127PN0CG",
                "model_codes": ["23127PN0CG", "23127PN0CC"],
                "regions": ["Global", "China"],
                "variants": (
                    {
                        "name": "Global",
                        "slug": "global",
                        "region": "Global",
                        "model_code": "23127PN0CG",
                    },
                    {
                        "name": "China",
                        "slug": "china",
                        "region": "China",
                        "model_code": "23127PN0CC",
                    },
                ),
            },
            {
                "name": "Redmi Note 13 Pro 5G",
                "slug": "redmi-note-13-pro-5g",
                "marketing_name": "Redmi Note 13 Pro 5G",
                "model_code": "2312DRA50G",
                "model_codes": ["2312DRA50G", "2312DRA50I"],
                "regions": ["Global", "India"],
                "variants": (
                    {
                        "name": "Global",
                        "slug": "global",
                        "region": "Global",
                        "model_code": "2312DRA50G",
                    },
                    {
                        "name": "India",
                        "slug": "india",
                        "region": "India",
                        "model_code": "2312DRA50I",
                    },
                ),
            },
        ),
    },
)

TOOL_CATEGORIES = (
    {
        "name": "Android platform tools",
        "slug": "android-platform-tools",
        "description": "Official Android developer tools and supported device workflows.",
        "icon": "smartphone",
        "sort_order": 1,
    },
    {
        "name": "OEM recovery utilities",
        "slug": "oem-recovery-utilities",
        "description": "Manufacturer-provided recovery utilities for supported devices.",
        "icon": "shield-check",
        "sort_order": 2,
    },
)

TOOLS = (
    {
        "name": "Android SDK Platform-Tools",
        "slug": "android-sdk-platform-tools",
        "category_slug": "android-platform-tools",
        "description": (
            "Google's official Android SDK command-line package, including ADB "
            "and Fastboot. Check device compatibility and the manufacturer's "
            "instructions before changing device software."
        ),
        "tool_type": FlashingTool.ToolType.OPEN_SOURCE,
        "platform": FlashingTool.Platform.MULTI,
        "risk_level": FlashingTool.RiskLevel.MODERATE,
        "official_url": "https://developer.android.com/tools/releases/platform-tools",
        "download_url": "https://developer.android.com/tools/releases/platform-tools",
        "features": ["ADB device communication", "Fastboot device management"],
        "requirements": ["A supported computer and USB connection"],
        "brand_slugs": [],
    },
    {
        "name": "Android Flash Tool",
        "slug": "android-flash-tool",
        "category_slug": "android-platform-tools",
        "description": (
            "Google's browser-based software installation workflow for supported "
            "Pixel devices. Confirm the exact model and follow Google's on-page "
            "compatibility and data-loss warnings."
        ),
        "tool_type": FlashingTool.ToolType.OEM,
        "platform": FlashingTool.Platform.MULTI,
        "risk_level": FlashingTool.RiskLevel.ADVANCED,
        "official_url": "https://flash.android.com/",
        "download_url": "",
        "features": ["Supported Pixel software installation", "Compatibility checks"],
        "requirements": ["A supported Pixel device and compatible browser"],
        "brand_slugs": ["google"],
    },
    {
        "name": "Motorola Software Fix",
        "slug": "motorola-software-fix",
        "category_slug": "oem-recovery-utilities",
        "description": (
            "Motorola's official recovery utility for supported Motorola devices. "
            "Check regional availability and device eligibility with Motorola."
        ),
        "tool_type": FlashingTool.ToolType.OEM,
        "platform": FlashingTool.Platform.WINDOWS,
        "risk_level": FlashingTool.RiskLevel.ADVANCED,
        "official_url": "https://en-us.support.motorola.com/app/softwarefix",
        "download_url": "https://en-us.support.motorola.com/app/softwarefix",
        "features": ["Manufacturer-supported software recovery"],
        "requirements": ["A supported Motorola device and Windows computer"],
        "brand_slugs": ["motorola"],
    },
)

SUPPORT_BRANDS = (
    {
        "name": "Google",
        "slug": "google",
        "website_url": "https://store.google.com/category/phones",
        "description": "Google Pixel device catalog.",
    },
    {
        "name": "Motorola",
        "slug": "motorola",
        "website_url": "https://www.motorola.com/",
        "description": "Motorola mobile device catalog.",
    },
)

MANUAL_POSTS = (
    {
        "title": "Check the exact model and region before choosing firmware",
        "slug": "check-model-and-region-before-firmware",
        "summary": (
            "A practical checklist for matching a device model code and region "
            "before selecting a firmware package."
        ),
        "seo_title": "Check a device model and region before firmware",
        "seo_description": (
            "Learn how to verify a phone's exact model code and region before "
            "choosing a firmware package."
        ),
        "body": (
            "<p>Phones sold under the same product name can have different "
            "hardware and software variants. Start with the complete model code "
            "shown in the device settings, on its packaging, or in the "
            "manufacturer's support information. Do not rely on a marketing name "
            "alone.</p>"
            "<h2>Before selecting a package</h2>"
            "<ul><li>Match the full model code, including its regional suffix.</li>"
            "<li>Check the package region and software channel against the "
            "device's current configuration.</li>"
            "<li>Compare the build identifier and release notes with the "
            "manufacturer's information.</li>"
            "<li>Use an official or otherwise authorized source and verify the "
            "file's published integrity information when available.</li>"
            "<li>Read the vendor's instructions and backup guidance before "
            "making any software change.</li></ul>"
            "<p>If the model, region, or package provenance cannot be confirmed, "
            "stop and ask the device manufacturer or an authorized service "
            "provider. A mismatched package can make a device unusable or erase "
            "its data.</p>"
        ),
    },
    {
        "title": "Understand common Android firmware package formats",
        "slug": "understand-android-firmware-package-formats",
        "summary": (
            "An original overview of OTA, recovery, fastboot, factory, service, "
            "and partition-image package labels."
        ),
        "seo_title": "Android firmware package formats explained",
        "seo_description": (
            "Understand common Android firmware package formats and why an exact "
            "model and vendor instructions matter."
        ),
        "body": (
            "<p>Firmware classification describes what is known about a record "
            "and its review status; package format describes how its files are "
            "organized or delivered. These are separate properties.</p>"
            "<h2>Common formats</h2>"
            "<ul><li><strong>Full OTA:</strong> a complete over-the-air update "
            "package intended for a supported update flow.</li>"
            "<li><strong>Incremental OTA:</strong> an update that depends on a "
            "specific earlier build.</li>"
            "<li><strong>Recovery ROM:</strong> a vendor recovery/update "
            "package.</li>"
            "<li><strong>Fastboot ROM:</strong> a package intended for a vendor-"
            "supported fastboot workflow.</li>"
            "<li><strong>Factory image:</strong> a manufacturer image bundle "
            "used in its documented factory workflow.</li>"
            "<li><strong>Service package:</strong> a manufacturer-specific "
            "service bundle.</li>"
            "<li><strong>Partition image:</strong> an individual system image, "
            "not necessarily a complete update.</li></ul>"
            "<p>Extensions alone do not prove compatibility or authenticity. "
            "Always confirm the model, region, build, source, and vendor "
            "instructions; if those details are missing, treat the package "
            "format as unknown.</p>"
        ),
    },
)


class Command(BaseCommand):
    help = "Load a small, idempotent device/tool catalog and draft editorial posts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the curated records without writing to the database.",
        )

    def handle(self, *args, **options):
        self._require_local_database()
        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run: 4 brands, 3 device models, 6 variants, "
                    "3 tools, and 2 draft posts."
                )
            )
            return

        missing_posts = [
            item
            for item in MANUAL_POSTS
            if not Post.objects.filter(slug=item["slug"]).exists()
        ]
        author = None
        if missing_posts:
            author = (
                get_user_model().objects.filter(is_staff=True).order_by("pk").first()
            )
            if author is None:
                raise CommandError(
                    "A staff author is required before creating manual draft posts."
                )

        created = {"brands": 0, "models": 0, "variants": 0, "tools": 0, "posts": 0}
        with transaction.atomic():
            brands = {}
            for data in (*BRANDS, *SUPPORT_BRANDS):
                brand, was_created = Brand.objects.get_or_create(
                    slug=data["slug"],
                    defaults={
                        "name": data["name"],
                        "website_url": data["website_url"],
                        "description": data["description"],
                        "is_featured": True,
                    },
                )
                brands[brand.slug] = brand
                created["brands"] += int(was_created)

            for brand_data in BRANDS:
                brand = brands[brand_data["slug"]]
                for model_data in brand_data["models"]:
                    variants_data = model_data["variants"]
                    model, was_created = Model.objects.get_or_create(
                        brand=brand,
                        slug=model_data["slug"],
                        defaults={
                            "name": model_data["name"],
                            "marketing_name": model_data["marketing_name"],
                            "model_code": model_data["model_code"],
                            "model_codes": model_data["model_codes"],
                            "regions": model_data["regions"],
                        },
                    )
                    created["models"] += int(was_created)
                    for variant_data in variants_data:
                        _, was_created = Variant.objects.get_or_create(
                            model=model,
                            slug=variant_data["slug"],
                            defaults={
                                key: value
                                for key, value in variant_data.items()
                                if key != "slug"
                            },
                        )
                        created["variants"] += int(was_created)

            categories = {}
            for data in TOOL_CATEGORIES:
                category, _ = FlashingToolCategory.objects.get_or_create(
                    slug=data["slug"],
                    defaults={
                        "name": data["name"],
                        "description": data["description"],
                        "icon": data["icon"],
                        "sort_order": data["sort_order"],
                    },
                )
                categories[category.slug] = category

            for data in TOOLS:
                tool_data = {
                    key: value
                    for key, value in data.items()
                    if key not in {"slug", "category_slug", "brand_slugs"}
                }
                tool, was_created = FlashingTool.objects.get_or_create(
                    slug=data["slug"],
                    defaults={
                        **tool_data,
                        "category": categories[data["category_slug"]],
                        "is_free": True,
                        "is_featured": True,
                    },
                )
                tool.supported_brands.add(
                    *(brands[slug] for slug in data["brand_slugs"])
                )
                created["tools"] += int(was_created)

            blog_category, _ = Category.objects.get_or_create(
                slug="firmware-literacy",
                defaults={"name": "Firmware literacy"},
            )
            for data in missing_posts:
                PostService().create_post(
                    title=data["title"],
                    body=data["body"],
                    author=author,
                    summary=data["summary"],
                    category=blog_category,
                    slug=data["slug"],
                    seo_title=data["seo_title"],
                    seo_description=data["seo_description"],
                    noindex=True,
                )
                created["posts"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Catalog loaded without deleting existing data: "
                + ", ".join(f"{count} {kind}" for kind, count in created.items())
            )
        )
        self.stdout.write(
            "No firmware binaries or downloadable firmware records were fabricated. "
            "New device models continue through the configured after-commit blog "
            "automation."
        )

    @staticmethod
    def _require_local_database() -> None:
        if connection.vendor == "sqlite":
            return
        host = connection.settings_dict.get("HOST") or ""
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise CommandError(
                "This catalog seed is limited to a local database; refusing "
                "the configured non-local database host."
            )
