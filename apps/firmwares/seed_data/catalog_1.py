"""Samsung, Xiaomi, Redmi, POCO, Google, OnePlus and Oppo device catalogue."""

from __future__ import annotations

from .chipsets import dim, exynos, helio, sd, tensor
from .types import (
    SCHEME_OPLUS,
    SCHEME_PIXEL,
    SCHEME_SAMSUNG,
    SCHEME_XIAOMI,
    BrandSeed,
    model,
    variant,
)

SAMSUNG = BrandSeed(
    name="Samsung",
    slug="samsung",
    website_url="https://www.samsung.com/",
    description=(
        "Samsung Galaxy smartphones and tablets. Samsung firmware is identified by "
        "the full model code (for example SM-S928B) and a regional CSC, and is "
        "flashed in Download mode."
    ),
    scheme=SCHEME_SAMSUNG,
    featured=True,
    models=(
        model(
            "Galaxy S24 Ultra",
            "SM-S928B",
            sd("8 Gen 3 for Galaxy"),
            (2024, 1),
            14,
            codename="e3q",
            marketing_name="Samsung Galaxy S24 Ultra",
            variants=(
                variant("Global", "Global", "SM-S928B"),
                variant("United States", "US", "SM-S928U"),
                variant("Korea", "KR", "SM-S928N"),
            ),
        ),
        model(
            "Galaxy S24",
            "SM-S921B",
            exynos("2400"),
            (2024, 1),
            14,
            codename="e1s",
            marketing_name="Samsung Galaxy S24",
            variants=(
                variant("Europe and global", "Global", "SM-S921B"),
                variant(
                    "United States",
                    "US",
                    "SM-S921U",
                    sd("8 Gen 3 for Galaxy"),
                ),
            ),
        ),
        model(
            "Galaxy A54 5G",
            "SM-A546B",
            exynos("1380"),
            (2023, 3),
            13,
            codename="a54x",
            marketing_name="Samsung Galaxy A54 5G",
        ),
        model(
            "Galaxy A35 5G",
            "SM-A356B",
            exynos("1380"),
            (2024, 3),
            14,
            codename="a35x",
            marketing_name="Samsung Galaxy A35 5G",
        ),
        model(
            "Galaxy A15",
            "SM-A155F",
            helio("G99"),
            (2023, 12),
            14,
            codename="a15",
            marketing_name="Samsung Galaxy A15",
        ),
        model(
            "Galaxy S23 FE",
            "SM-S711B",
            exynos("2200"),
            (2023, 10),
            13,
            codename="r11s",
            marketing_name="Samsung Galaxy S23 FE",
        ),
    ),
)

XIAOMI = BrandSeed(
    name="Xiaomi",
    slug="xiaomi",
    website_url="https://www.mi.com/global/",
    description=(
        "Xiaomi flagship smartphones. Xiaomi firmware is published per regional "
        "ROM (Global, China, India, EEA) and is matched by model code and "
        "codename."
    ),
    scheme=SCHEME_XIAOMI,
    featured=True,
    models=(
        model(
            "Xiaomi 14",
            "23127PN0CG",
            sd("8 Gen 3"),
            (2024, 2),
            14,
            codename="houji",
            marketing_name="Xiaomi 14",
            os_label="HyperOS",
            variants=(
                variant("Global", "Global", "23127PN0CG"),
                variant("China", "CN", "23127PN0CC"),
            ),
        ),
        model(
            "Xiaomi 14 Ultra",
            "24030PN60G",
            sd("8 Gen 3"),
            (2024, 3),
            14,
            codename="aurora",
            marketing_name="Xiaomi 14 Ultra",
            os_label="HyperOS",
        ),
        model(
            "Xiaomi 13T Pro",
            "23078PND5G",
            dim("9200 Plus"),
            (2023, 9),
            13,
            codename="corot",
            marketing_name="Xiaomi 13T Pro",
            os_label="MIUI 14",
        ),
        model(
            "Xiaomi 13",
            "2211133G",
            sd("8 Gen 2"),
            (2023, 2),
            13,
            codename="fuxi",
            marketing_name="Xiaomi 13",
            os_label="MIUI 14",
            variants=(
                variant("Global", "Global", "2211133G"),
                variant("China", "CN", "2211133C"),
            ),
        ),
        model(
            "Xiaomi 12T",
            "22071212AG",
            dim("8100 Ultra"),
            (2022, 10),
            12,
            codename="plato",
            marketing_name="Xiaomi 12T",
            os_label="MIUI 13",
        ),
        model(
            "Xiaomi 11 Lite 5G NE",
            "2109119DG",
            sd("778G"),
            (2021, 9),
            11,
            codename="lisa",
            marketing_name="Xiaomi 11 Lite 5G NE",
            os_label="MIUI 12.5",
        ),
    ),
)

REDMI = BrandSeed(
    name="Redmi",
    slug="redmi",
    website_url="https://www.mi.com/global/",
    description=(
        "Xiaomi's Redmi line of value smartphones. Redmi firmware shares the "
        "Xiaomi ROM naming and is matched by model code and codename."
    ),
    scheme=SCHEME_XIAOMI,
    featured=True,
    models=(
        model(
            "Redmi Note 13 Pro 5G",
            "2312DRA50G",
            sd("7s Gen 2"),
            (2024, 1),
            13,
            codename="garnet",
            marketing_name="Redmi Note 13 Pro 5G",
            os_label="MIUI 14",
            variants=(
                variant("Global", "Global", "2312DRA50G"),
                variant("India", "IN", "2312DRA50I"),
            ),
        ),
        model(
            "Redmi Note 13 Pro+ 5G",
            "23090RA98G",
            dim("7200 Ultra"),
            (2024, 1),
            13,
            codename="zircon",
            marketing_name="Redmi Note 13 Pro+ 5G",
            os_label="MIUI 14",
            variants=(
                variant("Global", "Global", "23090RA98G"),
                variant("India", "IN", "23090RA98I"),
            ),
        ),
        model(
            "Redmi Note 12 5G",
            "22111317G",
            sd("4 Gen 1"),
            (2023, 4),
            12,
            codename="sunstone",
            marketing_name="Redmi Note 12 5G",
            os_label="MIUI 13",
        ),
        model(
            "Redmi 13C",
            "23108RN04Y",
            helio("G85"),
            (2023, 12),
            13,
            codename="gale",
            marketing_name="Redmi 13C",
            os_label="MIUI 14",
        ),
        model(
            "Redmi 12",
            "23053RN02A",
            helio("G88"),
            (2023, 6),
            13,
            codename="fire",
            marketing_name="Redmi 12",
            os_label="MIUI 14",
        ),
        model(
            "Redmi Note 11",
            "2201117TG",
            sd("680"),
            (2022, 2),
            11,
            codename="spes",
            marketing_name="Redmi Note 11",
            os_label="MIUI 13",
        ),
    ),
)

POCO = BrandSeed(
    name="POCO",
    slug="poco",
    website_url="https://www.po.co/",
    description=(
        "Xiaomi's POCO performance-focused smartphones. POCO firmware shares the "
        "Xiaomi ROM naming and is matched by model code and codename."
    ),
    scheme=SCHEME_XIAOMI,
    featured=True,
    models=(
        model(
            "POCO F6",
            "24069PC21G",
            sd("8s Gen 3"),
            (2024, 5),
            14,
            codename="peridot",
            marketing_name="POCO F6",
            os_label="HyperOS",
            variants=(
                variant("Global", "Global", "24069PC21G"),
                variant("India", "IN", "24069PC21I"),
            ),
        ),
        model(
            "POCO X6 Pro 5G",
            "2311DRK48G",
            dim("8300 Ultra"),
            (2024, 1),
            14,
            codename="duchamp",
            marketing_name="POCO X6 Pro 5G",
            os_label="HyperOS",
            variants=(
                variant("Global", "Global", "2311DRK48G"),
                variant("India", "IN", "2311DRK48I"),
            ),
        ),
        model(
            "POCO X6 5G",
            "23122PCD1G",
            sd("7s Gen 2"),
            (2024, 1),
            14,
            codename="garnet",
            marketing_name="POCO X6 5G",
            os_label="HyperOS",
        ),
        model(
            "POCO M6 Pro 4G",
            "2312FPCA6G",
            helio("G99 Ultra"),
            (2024, 1),
            13,
            marketing_name="POCO M6 Pro 4G",
            os_label="MIUI 14",
        ),
        model(
            "POCO F5",
            "23049PCD8G",
            sd("7 Plus Gen 2"),
            (2023, 5),
            13,
            codename="marble",
            marketing_name="POCO F5",
            os_label="MIUI 14",
        ),
        model(
            "POCO C65",
            "2310FPCA4G",
            helio("G85"),
            (2023, 11),
            13,
            marketing_name="POCO C65",
            os_label="MIUI 14",
        ),
    ),
)

GOOGLE = BrandSeed(
    name="Google",
    slug="google",
    website_url="https://store.google.com/category/phones",
    description=(
        "Google Pixel phones. Pixel factory images and full OTA packages are "
        "published by Google per model codename and build ID."
    ),
    scheme=SCHEME_PIXEL,
    featured=True,
    models=(
        model(
            "Pixel 8 Pro",
            "GC3VE",
            tensor("G3"),
            (2023, 10),
            14,
            codename="husky",
            marketing_name="Google Pixel 8 Pro",
        ),
        model(
            "Pixel 8",
            "GKV4X",
            tensor("G3"),
            (2023, 10),
            14,
            codename="shiba",
            marketing_name="Google Pixel 8",
        ),
        model(
            "Pixel 8a",
            "G576D",
            tensor("G3"),
            (2024, 5),
            14,
            codename="akita",
            marketing_name="Google Pixel 8a",
        ),
        model(
            "Pixel 9",
            "GUR25",
            tensor("G4"),
            (2024, 8),
            14,
            codename="tokay",
            marketing_name="Google Pixel 9",
        ),
        model(
            "Pixel 7",
            "GVU6C",
            tensor("G2"),
            (2022, 10),
            13,
            codename="panther",
            marketing_name="Google Pixel 7",
        ),
        model(
            "Pixel 7a",
            "GWKK3",
            tensor("G2"),
            (2023, 5),
            13,
            codename="lynx",
            marketing_name="Google Pixel 7a",
        ),
        model(
            "Pixel 6a",
            "GB62Z",
            tensor(),
            (2022, 7),
            12,
            codename="bluejay",
            marketing_name="Google Pixel 6a",
        ),
    ),
)

ONEPLUS = BrandSeed(
    name="OnePlus",
    slug="oneplus",
    website_url="https://www.oneplus.com/",
    description=(
        "OnePlus smartphones. OnePlus firmware is published as full OTA packages "
        "per model code and region."
    ),
    scheme=SCHEME_OPLUS,
    featured=True,
    models=(
        model(
            "OnePlus 12",
            "CPH2581",
            sd("8 Gen 3"),
            (2024, 1),
            14,
            codename="waffle",
            marketing_name="OnePlus 12",
            variants=(
                variant("Global", "Global", "CPH2581"),
                variant("China", "CN", "PJD110"),
            ),
        ),
        model(
            "OnePlus 11 5G",
            "CPH2449",
            sd("8 Gen 2"),
            (2023, 2),
            13,
            codename="salami",
            marketing_name="OnePlus 11 5G",
            variants=(
                variant("Global", "Global", "CPH2449"),
                variant("China", "CN", "PBH110"),
            ),
        ),
        model(
            "OnePlus 10 Pro",
            "NE2213",
            sd("8 Gen 1"),
            (2022, 4),
            12,
            codename="negroni",
            marketing_name="OnePlus 10 Pro",
        ),
        model(
            "OnePlus Nord CE4",
            "CPH2613",
            sd("7 Gen 3"),
            (2024, 4),
            14,
            marketing_name="OnePlus Nord CE4",
        ),
        model(
            "OnePlus Nord 3 5G",
            "CPH2493",
            dim("9000"),
            (2023, 7),
            13,
            marketing_name="OnePlus Nord 3 5G",
        ),
        model(
            "OnePlus Nord CE 3 Lite 5G",
            "CPH2465",
            sd("695"),
            (2023, 4),
            13,
            marketing_name="OnePlus Nord CE 3 Lite 5G",
        ),
    ),
)

OPPO = BrandSeed(
    name="Oppo",
    slug="oppo",
    website_url="https://www.oppo.com/",
    description=(
        "Oppo smartphones running ColorOS. Oppo firmware is identified by the CPH "
        "(global) or PGxx (China) model code."
    ),
    scheme=SCHEME_OPLUS,
    models=(
        model(
            "Reno11 5G",
            "CPH2599",
            dim("7050"),
            (2023, 11),
            14,
            marketing_name="Oppo Reno11 5G",
        ),
        model(
            "Reno10 5G",
            "CPH2531",
            dim("7050"),
            (2023, 7),
            13,
            marketing_name="Oppo Reno10 5G",
        ),
        model(
            "Reno8 T 5G",
            "CPH2505",
            sd("695"),
            (2023, 2),
            13,
            marketing_name="Oppo Reno8 T 5G",
        ),
        model(
            "Find X6 Pro",
            "PGEM10",
            sd("8 Gen 2"),
            (2023, 3),
            13,
            marketing_name="Oppo Find X6 Pro",
        ),
        model(
            "A78 4G",
            "CPH2565",
            sd("680"),
            (2023, 1),
            13,
            marketing_name="Oppo A78 4G",
        ),
        model(
            "A58",
            "CPH2577",
            helio("G85"),
            (2023, 3),
            13,
            marketing_name="Oppo A58",
        ),
        model(
            "A17",
            "CPH2477",
            helio("G35"),
            (2022, 9),
            12,
            marketing_name="Oppo A17",
        ),
    ),
)

BRANDS_PART_1: tuple[BrandSeed, ...] = (
    SAMSUNG,
    XIAOMI,
    REDMI,
    POCO,
    GOOGLE,
    ONEPLUS,
    OPPO,
)
