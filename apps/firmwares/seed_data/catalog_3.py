"""Asus, Lenovo, Infinix, Tecno, Itel, ZTE, Nothing and Fairphone catalogue."""

from __future__ import annotations

from .chipsets import chipset_key, dim, helio, sd, unisoc
from .types import (
    SCHEME_ASUS,
    SCHEME_FAIRPHONE,
    SCHEME_LENOVO,
    SCHEME_NOTHING,
    SCHEME_TRANSSION,
    SCHEME_ZTE,
    BrandSeed,
    model,
)

ASUS = BrandSeed(
    name="Asus",
    slug="asus",
    website_url="https://www.asus.com/",
    description=(
        "Asus Zenfone and ROG Phone smartphones. Asus firmware is identified by "
        "the AIxxxx or ZSxxxx model code and a regional SKU."
    ),
    scheme=SCHEME_ASUS,
    models=(
        model(
            "ROG Phone 8 Pro",
            "AI2401",
            sd("8 Gen 3"),
            (2024, 1),
            14,
            marketing_name="Asus ROG Phone 8 Pro",
        ),
        model(
            "ROG Phone 7",
            "AI2205",
            sd("8 Gen 2"),
            (2023, 4),
            13,
            marketing_name="Asus ROG Phone 7",
        ),
        model(
            "ROG Phone 5",
            "ZS673KS",
            sd("888"),
            (2021, 3),
            11,
            marketing_name="Asus ROG Phone 5",
        ),
        model(
            "Zenfone 10",
            "AI2302",
            sd("8 Gen 2"),
            (2023, 6),
            13,
            marketing_name="Asus Zenfone 10",
        ),
        model(
            "Zenfone 9",
            "AI2202",
            sd("8 Plus Gen 1"),
            (2022, 7),
            12,
            marketing_name="Asus Zenfone 9",
        ),
        model(
            "Zenfone 8",
            "ZS590KS",
            sd("888"),
            (2021, 5),
            11,
            marketing_name="Asus Zenfone 8",
        ),
    ),
)

LENOVO = BrandSeed(
    name="Lenovo",
    slug="lenovo",
    website_url="https://www.lenovo.com/",
    description=(
        "Lenovo Android tablets and smartphones. Lenovo firmware is identified by "
        "the TB-xxxx (tablet) or Lxxxxx (phone) model code."
    ),
    scheme=SCHEME_LENOVO,
    models=(
        model(
            "Tab P12 Pro",
            "TB-Q706F",
            sd("870"),
            (2021, 7),
            11,
            marketing_name="Lenovo Tab P12 Pro",
        ),
        model(
            "Tab P11",
            "TB-J606F",
            helio("G70"),
            (2020, 11),
            10,
            marketing_name="Lenovo Tab P11",
        ),
        model(
            "Tab M10 (3rd Gen)",
            "TB-328FU",
            unisoc("T610"),
            (2022, 4),
            11,
            marketing_name="Lenovo Tab M10 (3rd Gen)",
        ),
        model(
            "Tab M8 (4th Gen)",
            "TB-300FU",
            helio("A22"),
            (2023, 2),
            12,
            marketing_name="Lenovo Tab M8 (4th Gen)",
        ),
        model(
            "Legion Phone Duel 2",
            "L70081",
            sd("8 Gen 1"),
            (2022, 4),
            12,
            marketing_name="Lenovo Legion Phone Duel 2",
        ),
        model(
            "Z6 Pro",
            "L78051",
            sd("855"),
            (2019, 4),
            9,
            marketing_name="Lenovo Z6 Pro",
        ),
    ),
)

INFINIX = BrandSeed(
    name="Infinix",
    slug="infinix",
    website_url="https://www.infinixmobility.com/",
    description=(
        "Infinix smartphones running XOS. Infinix firmware is identified by the "
        "Xnnnn model code and is flashed with scatter-file based tools."
    ),
    scheme=SCHEME_TRANSSION,
    models=(
        model(
            "Note 40 Pro",
            "X6850",
            helio("G99"),
            (2024, 3),
            14,
            marketing_name="Infinix Note 40 Pro",
        ),
        model(
            "Hot 40 Pro",
            "X6837",
            helio("G99"),
            (2023, 12),
            13,
            marketing_name="Infinix Hot 40 Pro",
        ),
        model(
            "Zero 30 5G",
            "X6731B",
            dim("8020"),
            (2023, 8),
            13,
            marketing_name="Infinix Zero 30 5G",
        ),
        model(
            "Note 30",
            "X6833B",
            helio("G99"),
            (2023, 5),
            13,
            marketing_name="Infinix Note 30",
        ),
        model(
            "Hot 30",
            "X6831",
            helio("G88"),
            (2023, 5),
            12,
            marketing_name="Infinix Hot 30",
        ),
        model(
            "Smart 8",
            "X6525",
            helio("G36"),
            (2023, 12),
            13,
            marketing_name="Infinix Smart 8",
        ),
    ),
)

TECNO = BrandSeed(
    name="Tecno",
    slug="tecno",
    website_url="https://www.tecno-mobile.com/",
    description=(
        "Tecno smartphones running HiOS. Tecno firmware is identified by the "
        "device code (for example CK7n) and is flashed with scatter-file based "
        "tools."
    ),
    scheme=SCHEME_TRANSSION,
    models=(
        model(
            "Camon 20 Pro",
            "CK7n",
            helio("G99"),
            (2023, 5),
            13,
            marketing_name="Tecno Camon 20 Pro",
        ),
        model(
            "Spark 20 Pro",
            "KJ6",
            helio("G99"),
            (2024, 1),
            13,
            marketing_name="Tecno Spark 20 Pro",
        ),
        model(
            "Spark 10 Pro",
            "KI7",
            helio("G88"),
            (2023, 3),
            13,
            marketing_name="Tecno Spark 10 Pro",
        ),
        model(
            "Pova 5",
            "LH7n",
            helio("G99"),
            (2023, 8),
            13,
            marketing_name="Tecno Pova 5",
        ),
        model(
            "Phantom X2",
            "AD8",
            dim("9000"),
            (2022, 12),
            12,
            marketing_name="Tecno Phantom X2",
        ),
        model(
            "Pop 7",
            "BF6",
            helio("A22"),
            (2023, 1),
            12,
            marketing_name="Tecno Pop 7",
        ),
    ),
)

ITEL = BrandSeed(
    name="Itel",
    slug="itel",
    website_url="https://www.itel-life.com/",
    description=(
        "Itel entry-level smartphones. Itel firmware is identified by the device "
        "model code and is flashed with Unisoc or MediaTek download tools."
    ),
    scheme=SCHEME_TRANSSION,
    models=(
        model(
            "S23",
            "S665L",
            unisoc("T606"),
            (2023, 3),
            13,
            marketing_name="Itel S23",
        ),
        model(
            "S23+",
            "S666LN",
            unisoc("T616"),
            (2023, 9),
            13,
            marketing_name="Itel S23+",
        ),
        model(
            "P40",
            "P662LN",
            unisoc("T606"),
            (2023, 1),
            12,
            marketing_name="Itel P40",
        ),
        model(
            "P55 5G",
            "",
            dim("6080"),
            (2023, 12),
            13,
            marketing_name="Itel P55 5G",
        ),
        model(
            "A70",
            "A665L",
            "",
            (2023, 8),
            13,
            marketing_name="Itel A70",
        ),
    ),
)

ZTE = BrandSeed(
    name="ZTE",
    slug="zte",
    website_url="https://www.zte.com.cn/global/",
    description=(
        "ZTE Axon smartphones and the nubia Red Magic gaming line. ZTE firmware is "
        "identified by the model code (for example A2023H or NX729J)."
    ),
    scheme=SCHEME_ZTE,
    models=(
        model(
            "Axon 40 Ultra",
            "A2023H",
            sd("8 Gen 1"),
            (2022, 6),
            12,
            marketing_name="ZTE Axon 40 Ultra",
        ),
        model(
            "Axon 30 Ultra",
            "A2022",
            sd("888"),
            (2021, 5),
            11,
            marketing_name="ZTE Axon 30 Ultra",
        ),
        model(
            "Axon 30 5G",
            "A2322",
            sd("870"),
            (2021, 7),
            11,
            marketing_name="ZTE Axon 30 5G",
        ),
        model(
            "nubia Red Magic 8 Pro",
            "NX729J",
            sd("8 Gen 2"),
            (2022, 12),
            13,
            marketing_name="ZTE nubia Red Magic 8 Pro",
        ),
        model(
            "nubia Red Magic 7",
            "NX679J",
            sd("8 Gen 1"),
            (2022, 2),
            12,
            marketing_name="ZTE nubia Red Magic 7",
        ),
    ),
)

NOTHING = BrandSeed(
    name="Nothing",
    slug="nothing",
    website_url="https://nothing.tech/",
    description=(
        "Nothing and CMF smartphones running Nothing OS. Nothing firmware is "
        "published as full OTA packages and fastboot images per model code."
    ),
    scheme=SCHEME_NOTHING,
    featured=True,
    models=(
        model(
            "Phone (1)",
            "A063",
            sd("778G Plus"),
            (2022, 7),
            12,
            codename="Spacewar",
            marketing_name="Nothing Phone (1)",
        ),
        model(
            "Phone (2)",
            "A065",
            sd("8 Plus Gen 1"),
            (2023, 7),
            13,
            codename="Pong",
            marketing_name="Nothing Phone (2)",
        ),
        model(
            "Phone (2a)",
            "A142",
            dim("7200 Pro"),
            (2024, 3),
            14,
            codename="Pacman",
            marketing_name="Nothing Phone (2a)",
        ),
        model(
            "Phone (2a) Plus",
            "A142P",
            dim("7350 Pro"),
            (2024, 7),
            14,
            marketing_name="Nothing Phone (2a) Plus",
        ),
        model(
            "CMF Phone 1",
            "A001",
            dim("7300"),
            (2024, 7),
            14,
            codename="Tetris",
            marketing_name="CMF Phone 1",
        ),
        model(
            "Phone (3a)",
            "A059",
            sd("7s Gen 3"),
            (2025, 3),
            15,
            codename="Asteroids",
            marketing_name="Nothing Phone (3a)",
        ),
    ),
)

FAIRPHONE = BrandSeed(
    name="Fairphone",
    slug="fairphone",
    website_url="https://www.fairphone.com/",
    description=(
        "Fairphone modular, repairable smartphones. Fairphone publishes factory "
        "images and OTA updates per device."
    ),
    scheme=SCHEME_FAIRPHONE,
    models=(
        model(
            "Fairphone 6",
            "FP6",
            sd("7s Gen 3"),
            (2025, 6),
            15,
            marketing_name="Fairphone 6",
        ),
        model(
            "Fairphone 5",
            "FP5",
            chipset_key("Qualcomm", "QCM6490"),
            (2023, 9),
            13,
            marketing_name="Fairphone 5",
        ),
        model(
            "Fairphone 4",
            "FP4",
            sd("750G"),
            (2021, 10),
            11,
            marketing_name="Fairphone 4",
        ),
        model(
            "Fairphone 3+",
            "FP3+",
            sd("632"),
            (2020, 9),
            10,
            marketing_name="Fairphone 3+",
        ),
        model(
            "Fairphone 3",
            "FP3",
            sd("632"),
            (2019, 9),
            9,
            marketing_name="Fairphone 3",
        ),
    ),
)

BRANDS_PART_3: tuple[BrandSeed, ...] = (
    ASUS,
    LENOVO,
    INFINIX,
    TECNO,
    ITEL,
    ZTE,
    NOTHING,
    FAIRPHONE,
)
