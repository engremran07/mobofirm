"""
Management command to seed the database with realistic dummy firmware data.

Creates:
- 100 brands (Samsung, Apple, Xiaomi, etc.)
- 100+ models per brand variations
- Variants with chipsets
- Firmware files for testing

Usage: python manage.py seed_firmware_data [--brands=100] [--clear]
"""

import random
import uuid
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.firmwares.models import (
    Brand,
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    Variant,
)

# Realistic brand data
BRANDS_DATA = [
    # Major smartphone brands
    "Samsung",
    "Apple",
    "Xiaomi",
    "Huawei",
    "Oppo",
    "Vivo",
    "OnePlus",
    "Realme",
    "Motorola",
    "Nokia",
    "LG",
    "Sony",
    "Google",
    "Asus",
    "Lenovo",
    "ZTE",
    "Meizu",
    "Honor",
    "Tecno",
    "Infinix",
    "Itel",
    "Alcatel",
    "TCL",
    "HTC",
    "BlackBerry",
    "Sharp",
    "Panasonic",
    "Kyocera",
    "Coolpad",
    "Gionee",
    # Chinese brands
    "LeEco",
    "Nubia",
    "Red Magic",
    "Black Shark",
    "iQOO",
    "Poco",
    "Redmi",
    "Camon",
    "Spark",
    "Hot",
    "Note",
    "Phantom",
    "Zero",
    # Regional brands
    "Micromax",
    "Lava",
    "Karbonn",
    "Intex",
    "Spice",
    "BLU",
    "Cubot",
    "Umidigi",
    "Doogee",
    "Oukitel",
    "Ulefone",
    "Blackview",
    "AGM",
    "Cat",
    "Ruggear",
    # Feature phone brands
    "iTel",
    "Symphony",
    "Walton",
    "RFL",
    "Maximus",
    "Winstar",
    "Colors",
    # Japanese brands
    "Fujitsu",
    "NEC",
    "Toshiba",
    "Casio",
    # Korean brands
    "Pantech",
    "SKY",
    # Tablet brands
    "Wacom",
    "Chuwi",
    "Teclast",
    "Alldocube",
    # Chinese ODM
    "Elephone",
    "Vernee",
    "Leagoo",
    "Homtom",
    "Bluboo",
    "UMI",
    "Maze",
    # Emerging brands
    "Nothing",
    "Fairphone",
    "Essential",
    "Razer",
    "ROG",
    # Budget brands
    "Wiko",
    "Archos",
    "Prestigio",
    "BQ",
    "Fly",
    # Smart device brands
    "Amazon",
    "Facebook",
    "Oculus",
    "Garmin",
    "Fitbit",
    # Tablet-focused
    "Huion",
    "XP-Pen",
    "Gaomon",
    # Premium brands
    "Vertu",
    "8848",
    "Tonino Lamborghini",
    "Porsche Design",
]

# Model name patterns per brand type
MODEL_PATTERNS = {
    "Samsung": [
        "Galaxy S{n}",
        "Galaxy A{n}",
        "Galaxy M{n}",
        "Galaxy F{n}",
        "Galaxy Note {n}",
        "Galaxy Z Fold {n}",
        "Galaxy Z Flip {n}",
    ],
    "Apple": [
        "iPhone {n}",
        "iPhone {n} Pro",
        "iPhone {n} Pro Max",
        "iPhone SE ({n})",
        "iPad Pro {n}",
        "iPad Air {n}",
    ],
    "Xiaomi": [
        "Mi {n}",
        "Mi {n} Pro",
        "Mi {n} Ultra",
        "Mi Mix {n}",
        "Mi Note {n}",
        "Xiaomi {n}T",
        "Xiaomi {n}T Pro",
    ],
    "Huawei": ["P{n}", "P{n} Pro", "Mate {n}", "Mate {n} Pro", "Nova {n}", "Y{n}"],
    "Oppo": ["Find X{n}", "Reno {n}", "Reno {n} Pro", "A{n}", "F{n}"],
    "Vivo": ["X{n}", "X{n} Pro", "V{n}", "Y{n}", "S{n}"],
    "OnePlus": ["{n}", "{n} Pro", "{n}T", "{n} Nord", "Nord {n}", "Nord CE {n}"],
    "Realme": ["{n}", "{n} Pro", "{n} Pro+", "GT {n}", "GT Neo {n}", "Narzo {n}"],
    "Motorola": ["Moto G{n}", "Moto G{n} Power", "Moto E{n}", "Edge {n}", "Razr {n}"],
    "Nokia": ["Nokia {n}", "Nokia G{n}", "Nokia C{n}", "Nokia X{n}"],
    "Google": ["Pixel {n}", "Pixel {n} Pro", "Pixel {n}a", "Pixel {n} XL"],
    "default": ["Model {n}", "Pro {n}", "Max {n}", "Lite {n}", "Plus {n}"],
}

# Chipset data
CHIPSETS = {
    "Qualcomm": [
        "Snapdragon 8 Gen 3",
        "Snapdragon 8 Gen 2",
        "Snapdragon 8+ Gen 1",
        "Snapdragon 8 Gen 1",
        "Snapdragon 888+",
        "Snapdragon 888",
        "Snapdragon 870",
        "Snapdragon 865+",
        "Snapdragon 865",
        "Snapdragon 7+ Gen 2",
        "Snapdragon 7 Gen 1",
        "Snapdragon 778G+",
        "Snapdragon 778G",
        "Snapdragon 6 Gen 1",
        "Snapdragon 695",
        "Snapdragon 680",
        "Snapdragon 665",
        "Snapdragon 4 Gen 2",
        "Snapdragon 480+",
        "Snapdragon 480",
    ],
    "MediaTek": [
        "Dimensity 9300",
        "Dimensity 9200+",
        "Dimensity 9200",
        "Dimensity 9000+",
        "Dimensity 9000",
        "Dimensity 8300",
        "Dimensity 8200",
        "Dimensity 8100",
        "Dimensity 8000",
        "Dimensity 7200",
        "Dimensity 7050",
        "Dimensity 6100+",
        "Dimensity 6080",
        "Helio G99",
        "Helio G96",
        "Helio G88",
        "Helio G85",
        "Helio G35",
        "Helio P35",
    ],
    "Samsung": [
        "Exynos 2400",
        "Exynos 2200",
        "Exynos 2100",
        "Exynos 1480",
        "Exynos 1380",
        "Exynos 1280",
        "Exynos 990",
        "Exynos 9825",
        "Exynos 9820",
    ],
    "Apple": [
        "A17 Pro",
        "A17",
        "A16 Bionic",
        "A15 Bionic",
        "A14 Bionic",
        "A13 Bionic",
        "A12 Bionic",
        "M2",
        "M1",
    ],
    "HiSilicon": [
        "Kirin 9000s",
        "Kirin 9000",
        "Kirin 990",
        "Kirin 985",
        "Kirin 980",
        "Kirin 970",
        "Kirin 820",
        "Kirin 810",
        "Kirin 710",
    ],
    "Unisoc": [
        "Tiger T820",
        "Tiger T770",
        "Tiger T760",
        "Tiger T700",
        "Tiger T616",
        "Tiger T612",
        "Tiger T610",
        "SC9863A",
        "SC7731E",
    ],
    "Google": [
        "Tensor G3",
        "Tensor G2",
        "Tensor G1",
    ],
}

# Region codes
REGIONS = [
    ("GLB", "Global"),
    ("US", "USA"),
    ("EU", "Europe"),
    ("CN", "China"),
    ("IN", "India"),
    ("KR", "Korea"),
    ("JP", "Japan"),
    ("TW", "Taiwan"),
    ("RU", "Russia"),
    ("BR", "Brazil"),
    ("LATAM", "Latin America"),
    ("MEA", "Middle East & Africa"),
    ("SEA", "Southeast Asia"),
]

# Android versions
ANDROID_VERSIONS = [
    "14.0",
    "13.0",
    "12.0",
    "12L",
    "11.0",
    "10.0",
    "9.0",
    "8.1",
    "8.0",
    "7.1",
    "7.0",
]

# RAM/Storage options
RAM_OPTIONS = [[4], [6], [8], [12], [16], [4, 6], [6, 8], [8, 12], [8, 12, 16]]
STORAGE_OPTIONS = [
    [64],
    [128],
    [256],
    [512],
    [64, 128],
    [128, 256],
    [256, 512],
    [128, 256, 512],
]


class Command(BaseCommand):
    help = "Seed the database with realistic dummy firmware data for testing"

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
            help="Average models per brand (default: 5)",
        )
        parser.add_argument(
            "--firmwares",
            type=int,
            default=500,
            help="Number of firmware files to create (default: 500)",
        )
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing data before seeding"
        )

    def handle(self, *args, **options):
        num_brands = options["brands"]
        models_per_brand = options["models_per_brand"]
        num_firmwares = options["firmwares"]
        clear = options["clear"]

        if clear:
            self.stdout.write(self.style.WARNING("Clearing existing data..."))
            OfficialFirmware.objects.all().delete()
            EngineeringFirmware.objects.all().delete()
            ReadbackFirmware.objects.all().delete()
            ModifiedFirmware.objects.all().delete()
            OtherFirmware.objects.all().delete()
            Variant.objects.all().delete()
            Model.objects.all().delete()
            Brand.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("✓ Cleared existing data"))

        # Create brands
        self.stdout.write(f"\nCreating {num_brands} brands...")
        brands = self._create_brands(num_brands)
        self.stdout.write(self.style.SUCCESS(f"✓ Created {len(brands)} brands"))

        # Create models
        self.stdout.write(f"\nCreating models (~{models_per_brand} per brand)...")
        models = self._create_models(brands, models_per_brand)
        self.stdout.write(self.style.SUCCESS(f"✓ Created {len(models)} models"))

        # Create variants
        self.stdout.write("\nCreating variants with chipsets...")
        variants = self._create_variants(models)
        self.stdout.write(self.style.SUCCESS(f"✓ Created {len(variants)} variants"))

        # Create firmware files
        self.stdout.write(f"\nCreating {num_firmwares} firmware files...")
        firmwares = self._create_firmwares(variants, num_firmwares)
        self.stdout.write(self.style.SUCCESS(f"✓ Created {firmwares} firmware files"))

        # Summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("✓ Seed complete!"))
        self.stdout.write(f"  Brands: {Brand.objects.count()}")
        self.stdout.write(f"  Models: {Model.objects.count()}")
        self.stdout.write(f"  Variants: {Variant.objects.count()}")
        self.stdout.write(f"  Official Firmwares: {OfficialFirmware.objects.count()}")
        self.stdout.write(
            f"  Engineering Firmwares: {EngineeringFirmware.objects.count()}"
        )
        self.stdout.write(f"  Readback Firmwares: {ReadbackFirmware.objects.count()}")
        self.stdout.write(f"  Modified Firmwares: {ModifiedFirmware.objects.count()}")
        self.stdout.write(f"  Other Firmwares: {OtherFirmware.objects.count()}")

    def _create_brands(self, count: int) -> list:
        brands = []
        brand_names = (
            BRANDS_DATA[:count]
            if count <= len(BRANDS_DATA)
            else BRANDS_DATA + [f"Brand{i}" for i in range(count - len(BRANDS_DATA))]
        )

        for name in brand_names[:count]:
            slug = slugify(name)
            brand, created = Brand.objects.get_or_create(
                slug=slug, defaults={"name": name}
            )
            if created:
                brands.append(brand)

        return brands

    def _create_models(self, brands: list, avg_per_brand: int) -> list:
        models = []

        for brand in brands:
            # Get patterns for this brand
            patterns = MODEL_PATTERNS.get(brand.name, MODEL_PATTERNS["default"])

            # Randomize number of models (avg_per_brand +/- 2)
            num_models = max(1, random.randint(avg_per_brand - 2, avg_per_brand + 2))  # noqa: S311

            for i in range(num_models):
                pattern = random.choice(patterns)  # noqa: S311
                # Use different number ranges
                num = random.choice(  # noqa: S311
                    [str(i) for i in range(1, 20)]
                    + [
                        "10",
                        "11",
                        "12",
                        "13",
                        "14",
                        "15",
                        "20",
                        "21",
                        "22",
                        "23",
                        "24",
                        "50",
                        "60",
                        "70",
                        "80",
                        "90",
                        "100",
                        "200",
                    ]
                )

                name = pattern.format(n=num)
                slug = slugify(name)

                # Avoid duplicates
                if Model.objects.filter(brand=brand, slug=slug).exists():
                    continue

                model = Model.objects.create(
                    brand=brand,
                    name=name,
                    slug=slug,
                    marketing_name=f"{brand.name} {name}",
                    model_code=self._generate_model_code(brand.name),
                    description=f"The {brand.name} {name} is a feature-rich smartphone offering excellent performance and value.",
                    release_date=self._random_release_date(),
                    is_active=random.random() > 0.1,  # 90% active  # noqa: S311
                )
                models.append(model)

        return models

    def _create_variants(self, models: list) -> list:
        variants = []

        for model in models:
            # Create 1-4 variants per model
            num_variants = random.randint(1, 4)  # noqa: S311
            used_regions = set()

            for i in range(num_variants):  # noqa: B007
                # Pick a region
                available_regions = [r for r in REGIONS if r[0] not in used_regions]
                if not available_regions:
                    break

                region_code, region_name = random.choice(available_regions)  # noqa: S311
                used_regions.add(region_code)

                # Pick a chipset based on brand
                chipset = self._get_chipset_for_brand(model.brand.name)

                name = region_name
                slug = slugify(f"{model.name}-{region_code}")

                variant = Variant.objects.create(
                    model=model,
                    name=name,
                    slug=slug,
                    region=region_code,
                    board_id=f"{model.brand.name[:3].upper()}-{random.randint(1000, 9999)}",  # noqa: S311
                    chipset=chipset,
                    ram_options=random.choice(RAM_OPTIONS),  # noqa: S311
                    storage_options=random.choice(STORAGE_OPTIONS),  # noqa: S311
                    is_active=model.is_active,
                )
                variants.append(variant)

        return variants

    def _create_firmwares(self, variants: list, count: int) -> int:
        if not variants:
            return 0

        firmware_types = [
            (OfficialFirmware, 0.5),  # 50% official
            (EngineeringFirmware, 0.2),  # 20% engineering
            (ReadbackFirmware, 0.1),  # 10% readback
            (ModifiedFirmware, 0.15),  # 15% modified
            (OtherFirmware, 0.05),  # 5% other
        ]

        created = 0
        for _ in range(count):
            # Pick a random variant
            variant = random.choice(variants)  # noqa: S311
            model = variant.model
            brand = model.brand

            # Pick firmware type based on weights
            fw_type = random.choices(  # noqa: S311
                [t[0] for t in firmware_types], weights=[t[1] for t in firmware_types]
            )[0]

            # Generate firmware data
            android_ver = random.choice(ANDROID_VERSIONS)  # noqa: S311
            build_date = self._random_release_date()

            fw_data = {
                "original_file_name": self._generate_filename(
                    brand.name, model.name, variant.region, android_ver
                ),
                "stored_file_path": f"/storage/firmwares/{brand.slug}/{model.slug}/{uuid.uuid4()}.zip",
                "brand": brand,
                "model": model,
                "variant": variant,
                "chipset": variant.chipset,
                "android_version": android_ver,
                "security_patch": build_date,
                "build_date": build_date,
                "build_number": self._generate_build_number(),
                "file_size": random.randint(  # noqa: S311
                    500_000_000, 8_000_000_000
                ),  # 500MB to 8GB
                "file_hash": uuid.uuid4().hex + uuid.uuid4().hex[:32],
                "partitions": self._generate_partitions(),
                "is_password_protected": random.random()  # noqa: S311
                < 0.1,  # 10% password protected
                "download_count": random.randint(0, 50000),  # noqa: S311
                "view_count": random.randint(0, 100000),  # noqa: S311
                "is_verified": random.random() > 0.3,  # 70% verified  # noqa: S311
                "is_active": random.random() > 0.1,  # 90% active  # noqa: S311
            }

            # Add subtype for certain firmware types
            if fw_type == EngineeringFirmware:
                fw_data["subtype"] = random.choice(  # noqa: S311
                    ["Factory", "QC", "Debug", "Test", "PreRelease"]
                )
            elif fw_type == ModifiedFirmware:
                fw_data["subtype"] = random.choice(  # noqa: S311
                    ["Root", "Custom ROM", "Debloated", "Optimized"]
                )
            elif fw_type == OtherFirmware:
                fw_data["subtype"] = random.choice(  # noqa: S311
                    ["Recovery", "TWRP", "Bootloader", "Kernel"]
                )

            fw_type.objects.create(**fw_data)
            created += 1

        return created

    def _generate_model_code(self, brand_name: str) -> str:
        prefixes = {
            "Samsung": "SM-",
            "Apple": "A",
            "Xiaomi": "M",
            "Huawei": "ANA-",
            "Oppo": "CPH",
            "Vivo": "V",
            "OnePlus": "IN",
            "Realme": "RMX",
            "Motorola": "XT",
            "Nokia": "TA-",
            "Google": "G",
        }
        prefix = prefixes.get(brand_name, "MDL-")
        return f"{prefix}{random.randint(1000, 9999)}"  # noqa: S311

    def _random_release_date(self) -> date:
        days_ago = random.randint(30, 1500)  # Within last 4 years  # noqa: S311
        return date.today() - timedelta(days=days_ago)

    def _get_chipset_for_brand(self, brand_name: str) -> str:
        brand_chipsets = {
            "Apple": CHIPSETS["Apple"],
            "Samsung": CHIPSETS["Samsung"] + CHIPSETS["Qualcomm"],
            "Huawei": CHIPSETS["HiSilicon"] + CHIPSETS["Qualcomm"],
            "Honor": CHIPSETS["HiSilicon"]
            + CHIPSETS["Qualcomm"]
            + CHIPSETS["MediaTek"],
            "Google": CHIPSETS["Google"],
        }

        chipset_list = brand_chipsets.get(
            brand_name, CHIPSETS["Qualcomm"] + CHIPSETS["MediaTek"] + CHIPSETS["Unisoc"]
        )
        return random.choice(chipset_list)  # noqa: S311

    def _generate_filename(
        self, brand: str, model: str, region: str, android: str
    ) -> str:
        safe_brand = brand.replace(" ", "_")
        safe_model = model.replace(" ", "_")
        version = (
            f"V{random.randint(1, 15)}.{random.randint(0, 9)}.{random.randint(0, 99)}"  # noqa: S311
        )
        return f"{safe_brand}_{safe_model}_{region}_Android{android}_{version}.zip"

    def _generate_build_number(self) -> str:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return f"{random.choice(letters)}{random.choice(letters)}{random.choice(letters)}{random.randint(10, 99)}.{random.randint(100, 999)}"  # noqa: S311

    def _generate_partitions(self) -> list:
        all_partitions = [
            "boot",
            "recovery",
            "system",
            "vendor",
            "product",
            "odm",
            "userdata",
            "cache",
            "metadata",
            "vbmeta",
            "dtbo",
            "super",
            "modem",
            "persist",
            "misc",
            "aboot",
            "cmnlib",
            "keymaster",
            "rpm",
            "tz",
            "hyp",
            "devcfg",
            "dsp",
            "bluetooth",
            "logo",
        ]
        num_partitions = random.randint(5, 15)  # noqa: S311
        return random.sample(all_partitions, num_partitions)
