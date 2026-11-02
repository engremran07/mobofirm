"""
Management command to populate dummy firmware data for testing.
Creates ~100 brands, ~500 models with variants, and firmware entries
to test automated SEO, blog posts, tags, and distribution features.
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.firmwares.models import (
    Brand,
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    PendingFirmware,
    Variant,
)
from apps.firmwares.tracking_models import FirmwareRequest


class Command(BaseCommand):
    help = "Populate extensive dummy firmware data for testing automation features"

    def add_arguments(self, parser):
        parser.add_argument(
            "--brands",
            type=int,
            default=100,
            help="Number of brands to create (default: 100)",
        )
        parser.add_argument(
            "--models-per-brand",
            type=int,
            default=5,
            help="Average models per brand (default: 5, creates ~500 total)",
        )

    def handle(self, *args, **options):
        num_brands = options["brands"]
        models_per_brand = options["models_per_brand"]

        self.stdout.write(self.style.SUCCESS("Creating extensive firmware data..."))
        self.stdout.write(
            self.style.WARNING(
                f"Target: {num_brands} brands, ~{num_brands * models_per_brand} models"
            )
        )

        # Generate realistic brand names
        brand_prefixes = [
            "Samsung",
            "Xiaomi",
            "OPPO",
            "Vivo",
            "Realme",
            "OnePlus",
            "Huawei",
            "Motorola",
            "Nokia",
            "Sony",
            "LG",
            "HTC",
            "Asus",
            "Lenovo",
            "ZTE",
            "Meizu",
            "Infinix",
            "Tecno",
            "iTel",
            "Gionee",
            "Coolpad",
            "LeEco",
            "Poco",
            "Redmi",
            "Honor",
            "iQOO",
            "Nothing",
            "Micromax",
            "Lava",
            "Karbonn",
            "Intex",
            "Xolo",
            "Panasonic",
            "Sharp",
            "Fujitsu",
            "Alcatel",
            "TCL",
            "BLU",
            "Wiko",
            "Archos",
            "Vertex",
            "Doogee",
            "Blackview",
            "Ulefone",
            "Oukitel",
            "Cubot",
            "Elephone",
            "Umidigi",
            "Leagoo",
            "Homtom",
            "Vernee",
            "Bluboo",
            "Maze",
            "Nomu",
            "AGM",
        ]

        brand_suffixes = [
            "",
            " Mobile",
            " Telecom",
            " Electronics",
            " Tech",
            " Devices",
            " Communications",
            " Digital",
            " Systems",
            " Networks",
        ]

        brands_data = []
        existing_names = set()

        for i in range(num_brands):
            if i < len(brand_prefixes):
                name = brand_prefixes[i] + random.choice(brand_suffixes)  # noqa: S311
            else:
                name = f"{random.choice(brand_prefixes)} {random.choice(['Pro', 'Plus', 'Max', 'Ultra', 'Lite', 'Neo', 'Ace', 'Edge', 'Prime'])}"  # noqa: S311

            # Ensure unique names
            counter = 1
            original_name = name
            while name in existing_names:
                name = f"{original_name} {counter}"
                counter += 1
            existing_names.add(name)

            slug = slugify(name)
            # Ensure unique slugs
            counter = 1
            original_slug = slug
            while slug in [b["slug"] for b in brands_data]:
                slug = f"{original_slug}-{counter}"
                counter += 1

            brands_data.append({"name": name, "slug": slug})

        # Create brands
        brands = {}
        created_count = 0
        for brand_data in brands_data:
            brand, created = Brand.objects.get_or_create(
                slug=brand_data["slug"], defaults={"name": brand_data["name"]}
            )
            brands[brand_data["slug"]] = brand
            if created:
                created_count += 1

        self.stdout.write(
            f"✓ Brands: {created_count} created, {len(brands_data) - created_count} existing"
        )

        # Generate model names and variants
        model_series = [
            "Galaxy",
            "Redmi",
            "Note",
            "Find",
            "Reno",
            "GT",
            "Nord",
            "Mate",
            "P Series",
            "Nova",
            "Y Series",
            "A Series",
            "M Series",
            "F Series",
            "Narzo",
            "C Series",
            "Hot",
            "Spark",
            "Camon",
            "Pova",
            "Pop",
            "Mix",
            "Pad",
            "Book",
            "Watch",
            "Band",
            "Buds",
        ]

        model_modifiers = [
            "",
            " Pro",
            " Max",
            " Ultra",
            " Plus",
            " Lite",
            " SE",
            " Neo",
            " Ace",
            " Edge",
            " Prime",
            " Youth",
            " Standard",
            " Turbo",
            " Racing",
            " GT",
            " Gaming",
            " 5G",
        ]

        regions = [
            "Global",
            "USA",
            "Europe",
            "India",
            "China",
            "Korea",
            "Japan",
            "LATAM",
            "MEA",
        ]
        chipsets = [
            "Snapdragon 8 Gen 3",
            "Snapdragon 8 Gen 2",
            "Snapdragon 888",
            "Snapdragon 870",
            "Snapdragon 780G",
            "Snapdragon 750G",
            "Snapdragon 695",
            "Snapdragon 480",
            "MediaTek Dimensity 9200",
            "MediaTek Dimensity 8200",
            "MediaTek Dimensity 7200",
            "MediaTek Dimensity 6020",
            "MediaTek Helio G99",
            "MediaTek Helio G96",
            "Exynos 2400",
            "Exynos 1380",
            "Exynos 1330",
            "Exynos 850",
            "Kirin 9000",
            "Kirin 990",
            "Kirin 985",
            "Unisoc T820",
        ]

        android_versions = [
            "Android 15",
            "Android 14",
            "Android 13",
            "Android 12",
            "Android 11",
        ]

        created_variants = []
        models_created = 0
        variants_created = 0

        for brand_slug, brand in brands.items():
            # Each brand gets 4-7 models
            num_models = random.randint(models_per_brand - 2, models_per_brand + 2)  # noqa: S311

            for i in range(num_models):  # noqa: B007
                model_name = f"{random.choice(model_series)} {random.randint(1, 99)}{random.choice(model_modifiers)}"  # noqa: S311
                model_slug = slugify(f"{model_name}-{brand_slug}")

                model, created = Model.objects.get_or_create(
                    brand=brand, slug=model_slug, defaults={"name": model_name}
                )

                if created:
                    models_created += 1

                # Each model gets 1-4 variants
                num_variants = random.randint(1, 4)  # noqa: S311
                for v in range(num_variants):  # noqa: B007
                    # Generate variant code
                    if (
                        "samsung" in brand_slug.lower()
                        or "galaxy" in model_name.lower()
                    ):
                        variant_code = f"SM-{chr(65 + random.randint(0, 25))}{random.randint(100, 999)}{chr(65 + random.randint(0, 25))}"  # noqa: S311
                    elif (
                        "xiaomi" in brand_slug.lower() or "redmi" in brand_slug.lower()
                    ):
                        variant_code = f"{random.randint(2020, 2024)}{random.randint(10, 99)}{random.choice(['RN', 'RA', 'CPH'])}{random.randint(10, 99)}{chr(65 + random.randint(0, 25))}"  # noqa: S311
                    elif "oppo" in brand_slug.lower():
                        variant_code = f"CPH{random.randint(2000, 2999)}"  # noqa: S311
                    elif "vivo" in brand_slug.lower():
                        variant_code = f"V{random.randint(2000, 2999)}"  # noqa: S311
                    elif "realme" in brand_slug.lower():
                        variant_code = f"RMX{random.randint(3000, 3999)}"  # noqa: S311
                    else:
                        variant_code = f"{chr(65 + random.randint(0, 25))}{random.randint(1000, 9999)}"  # noqa: S311

                    variant_slug = slugify(variant_code)
                    board_id = f"{random.choice(['sm', 's5e', 'mt', 'sdm', 'msm'])}{random.randint(6000, 9999)}"  # noqa: S311

                    variant, created = Variant.objects.get_or_create(
                        model=model,
                        slug=variant_slug,
                        defaults={
                            "name": variant_code,
                            "region": random.choice(regions),  # noqa: S311
                            "board_id": board_id,
                        },
                    )

                    if created:
                        variants_created += 1
                        created_variants.append(variant)

        self.stdout.write(f"✓ Models: {models_created} created")
        self.stdout.write(f"✓ Variants: {variants_created} created")
        self.stdout.write(
            self.style.WARNING(
                f"Generating firmwares for {len(created_variants)} variants..."
            )
        )

        # Create firmware entries for each variant

        firmware_count = 0
        official_count = engineering_count = modified_count = 0

        for idx, variant in enumerate(created_variants, 1):
            if idx % 50 == 0:
                self.stdout.write(
                    f"  Progress: {idx}/{len(created_variants)} variants..."
                )

            # Each variant gets 2-5 firmware entries
            num_firmwares = random.randint(2, 5)  # noqa: S311

            for i in range(num_firmwares):  # noqa: B007
                # Select firmware type based on weighted probability
                rand_val = random.randint(1, 100)  # noqa: S311
                if rand_val <= 60:
                    _firmware_type_name, FirmwareClass = "official", OfficialFirmware
                    official_count += 1
                elif rand_val <= 80:
                    _firmware_type_name, FirmwareClass = (
                        "engineering",
                        EngineeringFirmware,
                    )
                    engineering_count += 1
                else:
                    _firmware_type_name, FirmwareClass = "modified", ModifiedFirmware
                    modified_count += 1

                version = f"V{random.randint(1, 15)}.{random.randint(0, 9)}.{random.randint(0, 199)}"  # noqa: S311
                chipset = random.choice(chipsets)  # noqa: S311
                android = random.choice(android_versions)  # noqa: S311
                build_date = timezone.now() - timedelta(
                    days=random.randint(1, 730)  # noqa: S311
                )  # Up to 2 years old

                firmware_data = {
                    "brand": variant.model.brand,
                    "model": variant.model,
                    "variant": variant,
                    "chipset": chipset,
                    "original_file_name": f"{variant.slug}_{version.replace('.', '_')}.zip",
                    "stored_file_path": f"firmwares/{variant.model.brand.slug}/{variant.model.slug}/{variant.slug}_{version}.zip",
                    "uploader": None,
                    "metadata": {
                        "version": version,
                        "android_version": android,
                        "file_size_bytes": random.randint(  # noqa: S311
                            1500000000, 6000000000
                        ),  # 1.5-6GB
                        "build_date": build_date.isoformat(),
                        "security_patch": build_date.strftime("%Y-%m"),
                        "build_fingerprint": f"{variant.model.brand.slug}/{variant.model.slug}/{variant.slug}:{android}/{version}:user/release-keys",
                    },
                }

                # Add type-specific fields
                if FirmwareClass == ModifiedFirmware:
                    firmware_data["subtype"] = random.choice(  # noqa: S311
                        [
                            "Root",
                            "Custom Recovery",
                            "Debloated",
                            "Performance Mod",
                            "Camera Mod",
                            "Battery Optimization",
                            "Kernel",
                            "ROM",
                        ]
                    )
                elif FirmwareClass == EngineeringFirmware:
                    firmware_data["subtype"] = random.choice(  # noqa: S311
                        [
                            "Engineering Boot",
                            "Factory Test",
                            "Debug Build",
                            "Diagnostic",
                        ]
                    )

                _firmware, created = FirmwareClass.objects.get_or_create(
                    variant=variant,
                    chipset=chipset,
                    original_file_name=firmware_data["original_file_name"],
                    defaults=firmware_data,
                )

                if created:
                    firmware_count += 1

        self.stdout.write(f"✓ Firmwares: {firmware_count} created")
        self.stdout.write(f"  - Official: {official_count}")
        self.stdout.write(f"  - Engineering: {engineering_count}")
        self.stdout.write(f"  - Modified: {modified_count}")

        # Create some pending firmwares
        pending_count = 0
        sample_variants = random.sample(
            created_variants, min(30, len(created_variants))
        )

        for i, variant in enumerate(sample_variants):
            chipset = random.choice(chipsets)  # noqa: S311

            _pending, created = PendingFirmware.objects.get_or_create(
                original_file_name=f"{variant.slug}_pending_{i}.zip",
                defaults={
                    "stored_file_path": f"pending/{variant.slug}_pending_{i}.zip",
                    "uploader": None,
                    "uploaded_brand": variant.model.brand,
                    "uploaded_model": variant.model,
                    "uploaded_variant": variant,
                    "chipset": chipset,
                    "admin_decision": random.choice(  # noqa: S311
                        ["pending", "pending", "pending", "approved", "rejected"]
                    ),
                    "metadata": {
                        "version": f"V{random.randint(1, 15)}.{random.randint(0, 9)}.{random.randint(0, 99)}",  # noqa: S311
                        "uploaded_at": (
                            timezone.now() - timedelta(days=random.randint(1, 60))  # noqa: S311
                        ).isoformat(),
                    },
                },
            )
            if created:
                pending_count += 1

        self.stdout.write(f"✓ Pending firmwares: {pending_count} created")

        # Create firmware requests
        request_count = 0
        sample_variants_for_requests = random.sample(
            created_variants, min(50, len(created_variants))
        )

        for variant in sample_variants_for_requests:
            _request, created = FirmwareRequest.objects.get_or_create(
                brand=variant.model.brand,
                model=variant.model,
                variant_name=variant.name,
                firmware_type=random.choice(  # noqa: S311
                    ["official", "official", "engineering", "modified"]
                ),
                defaults={
                    "description": f"Please add {random.choice(['latest', 'stock', 'official', 'custom'])} firmware for {variant.model.name} {variant.name}",  # noqa: S311
                    "urgency": random.randint(1, 4),  # noqa: S311
                    "status": random.choice(  # noqa: S311
                        ["open", "open", "open", "in_progress", "fulfilled"]
                    ),
                    "request_count": random.randint(1, 50),  # noqa: S311
                    "requested_by": None,
                },
            )
            if created:
                request_count += 1

        self.stdout.write(f"✓ Firmware requests: {request_count} created")

        # Final summary
        self.stdout.write(self.style.SUCCESS("\n" + "=" * 60))
        self.stdout.write(self.style.SUCCESS("✓ Data population complete!"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"Brands:              {Brand.objects.count():,}")
        self.stdout.write(f"Models:              {Model.objects.count():,}")
        self.stdout.write(f"Variants:            {Variant.objects.count():,}")
        self.stdout.write(f"Official Firmwares:  {OfficialFirmware.objects.count():,}")
        self.stdout.write(
            f"Engineering Firmwares: {EngineeringFirmware.objects.count():,}"
        )
        self.stdout.write(f"Modified Firmwares:  {ModifiedFirmware.objects.count():,}")
        self.stdout.write(f"Pending Firmwares:   {PendingFirmware.objects.count():,}")
        self.stdout.write(f"Firmware Requests:   {FirmwareRequest.objects.count():,}")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.WARNING("\nNext steps:"))
        self.stdout.write("  1. Run: python manage.py generate_firmware_blogs --force")
        self.stdout.write("  2. Test automated SEO, tags, and blog posts")
        self.stdout.write("  3. Check Admin > Distribution > Distribution Settings")
        self.stdout.write("  4. View homepage to see stats, firmwares, and blogs")
        self.stdout.write(self.style.SUCCESS("=" * 60))
