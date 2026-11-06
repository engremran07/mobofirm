"""Vivo, Realme, Huawei, Honor, Motorola, Nokia and Sony device catalogue."""

from __future__ import annotations

from .chipsets import dim, helio, kirin, sd, unisoc
from .types import (
    SCHEME_HMD,
    SCHEME_HONOR,
    SCHEME_HUAWEI,
    SCHEME_MOTOROLA,
    SCHEME_OPLUS,
    SCHEME_SONY,
    SCHEME_VIVO,
    BrandSeed,
    model,
)

VIVO = BrandSeed(
    name="Vivo",
    slug="vivo",
    website_url="https://www.vivo.com/",
    description=(
        "Vivo smartphones running Funtouch OS and OriginOS. Vivo firmware is "
        "identified by the V-series model code."
    ),
    scheme=SCHEME_VIVO,
    models=(
        model(
            "V29 5G", "V2250", sd("778G"), (2023, 8), 13, marketing_name="Vivo V29 5G"
        ),
        model("V27", "V2231", dim("7200"), (2023, 3), 13, marketing_name="Vivo V27"),
        model("X100", "V2309", dim("9300"), (2023, 11), 14, marketing_name="Vivo X100"),
        model(
            "X90 Pro",
            "V2242",
            dim("9200"),
            (2022, 12),
            13,
            marketing_name="Vivo X90 Pro",
        ),
        model("Y36", "V2247", sd("680"), (2023, 5), 13, marketing_name="Vivo Y36"),
        model("Y22", "V2207", helio("G70"), (2022, 9), 12, marketing_name="Vivo Y22"),
    ),
)

REALME = BrandSeed(
    name="Realme",
    slug="realme",
    website_url="https://www.realme.com/",
    description=(
        "Realme smartphones running Realme UI. Realme firmware is identified by "
        "the RMX model code."
    ),
    scheme=SCHEME_OPLUS,
    models=(
        model(
            "Realme 12 Pro+ 5G",
            "RMX3840",
            sd("7s Gen 2"),
            (2024, 1),
            14,
            marketing_name="Realme 12 Pro+ 5G",
        ),
        model(
            "Realme 11 Pro+ 5G",
            "RMX3741",
            dim("7050"),
            (2023, 6),
            13,
            marketing_name="Realme 11 Pro+ 5G",
        ),
        model(
            "Realme 10 Pro 5G",
            "RMX3661",
            sd("695"),
            (2022, 12),
            13,
            marketing_name="Realme 10 Pro 5G",
        ),
        model(
            "Realme Narzo 60 5G",
            "RMX3750",
            dim("6020"),
            (2023, 7),
            13,
            marketing_name="Realme Narzo 60 5G",
        ),
        model(
            "Realme C55",
            "RMX3710",
            helio("G88"),
            (2023, 3),
            13,
            marketing_name="Realme C55",
        ),
        model(
            "Realme C53",
            "RMX3760",
            unisoc("T612"),
            (2023, 7),
            13,
            marketing_name="Realme C53",
        ),
    ),
)

HUAWEI = BrandSeed(
    name="Huawei",
    slug="huawei",
    website_url="https://consumer.huawei.com/",
    description=(
        "Huawei smartphones from the Android and EMUI era. Huawei firmware is "
        "identified by the model code (for example ELS-NX9) and a regional "
        "customization pack."
    ),
    scheme=SCHEME_HUAWEI,
    models=(
        model(
            "P40 Pro",
            "ELS-NX9",
            kirin("990 5G"),
            (2020, 4),
            10,
            marketing_name="Huawei P40 Pro",
            os_label="EMUI 10.1",
        ),
        model(
            "P40 lite",
            "JNY-LX1",
            kirin("810"),
            (2020, 3),
            10,
            marketing_name="Huawei P40 lite",
            os_label="EMUI 10.1",
        ),
        model(
            "P30 Pro",
            "VOG-L29",
            kirin("980"),
            (2019, 3),
            9,
            marketing_name="Huawei P30 Pro",
            os_label="EMUI 9.1",
        ),
        model(
            "Mate 20 Pro",
            "LYA-L29",
            kirin("980"),
            (2018, 10),
            9,
            marketing_name="Huawei Mate 20 Pro",
            os_label="EMUI 9.0",
        ),
        model(
            "P20 Pro",
            "CLT-L29",
            kirin("970"),
            (2018, 3),
            8,
            marketing_name="Huawei P20 Pro",
            os_label="EMUI 8.1",
        ),
        model(
            "nova 9",
            "NAM-LX9",
            sd("778G"),
            (2021, 10),
            11,
            marketing_name="Huawei nova 9",
            os_label="EMUI 12.0",
        ),
    ),
)

HONOR = BrandSeed(
    name="Honor",
    slug="honor",
    website_url="https://www.honor.com/",
    description=(
        "Honor smartphones running Magic UI and MagicOS. Honor firmware is "
        "identified by the model code (for example REA-NX9)."
    ),
    scheme=SCHEME_HONOR,
    models=(
        model(
            "Magic6 Pro",
            "BVL-N49",
            sd("8 Gen 3"),
            (2024, 2),
            14,
            marketing_name="Honor Magic6 Pro",
            os_label="MagicOS 8.0",
        ),
        model(
            "Magic5 Pro",
            "PGT-N19",
            sd("8 Gen 2"),
            (2023, 3),
            13,
            marketing_name="Honor Magic5 Pro",
            os_label="MagicOS 7.1",
        ),
        model(
            "Honor 90",
            "REA-NX9",
            sd("7 Gen 1"),
            (2023, 7),
            13,
            marketing_name="Honor 90",
            os_label="MagicOS 7.1",
        ),
        model(
            "Honor 70",
            "FNE-NX9",
            sd("778G Plus"),
            (2022, 6),
            12,
            marketing_name="Honor 70",
            os_label="MagicOS 6.1",
        ),
        model(
            "Honor 50",
            "NTH-NX9",
            sd("778G"),
            (2021, 11),
            11,
            marketing_name="Honor 50",
            os_label="Magic UI 5.0",
        ),
        model(
            "Honor X8a",
            "CRT-LX1",
            helio("G88"),
            (2023, 2),
            12,
            marketing_name="Honor X8a",
            os_label="Magic UI 6.1",
        ),
    ),
)

MOTOROLA = BrandSeed(
    name="Motorola",
    slug="motorola",
    website_url="https://www.motorola.com/",
    description=(
        "Motorola smartphones. Motorola firmware is identified by the XT model "
        "code with a regional suffix (for example XT2165-5) and is flashed with "
        "Fastboot or the Rescue and Smart Assistant."
    ),
    scheme=SCHEME_MOTOROLA,
    models=(
        model(
            "Moto G84 5G",
            "XT2347-2",
            sd("695"),
            (2023, 8),
            13,
            marketing_name="Motorola Moto G84 5G",
        ),
        model(
            "Moto G73 5G",
            "XT2237-2",
            dim("930"),
            (2023, 3),
            13,
            marketing_name="Motorola Moto G73 5G",
        ),
        model(
            "Moto G53 5G",
            "XT2335-2",
            sd("480 Plus 5G"),
            (2022, 12),
            13,
            marketing_name="Motorola Moto G53 5G",
        ),
        model(
            "Moto G Power 5G (2023)",
            "XT2311-3",
            dim("930"),
            (2023, 1),
            13,
            marketing_name="Motorola Moto G Power 5G (2023)",
        ),
        model(
            "Moto G Play (2023)",
            "XT2271-5",
            sd("680"),
            (2023, 1),
            12,
            marketing_name="Motorola Moto G Play (2023)",
        ),
        model(
            "Moto G Power (2022)",
            "XT2165-5",
            helio("G37"),
            (2022, 3),
            11,
            marketing_name="Motorola Moto G Power (2022)",
        ),
    ),
)

NOKIA = BrandSeed(
    name="Nokia",
    slug="nokia",
    website_url="https://www.hmd.com/",
    description=(
        "Nokia-branded smartphones made by HMD. Nokia firmware is identified by "
        "the TA-xxxx model code."
    ),
    scheme=SCHEME_HMD,
    models=(
        model(
            "Nokia G42 5G",
            "TA-1581",
            sd("480 Plus 5G"),
            (2023, 7),
            13,
            marketing_name="Nokia G42 5G",
        ),
        model(
            "Nokia X30 5G",
            "TA-1450",
            sd("695"),
            (2022, 10),
            12,
            marketing_name="Nokia X30 5G",
        ),
        model(
            "Nokia C32",
            "TA-1534",
            unisoc("SC9863A"),
            (2023, 2),
            13,
            marketing_name="Nokia C32",
        ),
        model(
            "Nokia G21",
            "TA-1418",
            unisoc("T606"),
            (2022, 2),
            11,
            marketing_name="Nokia G21",
        ),
        model(
            "Nokia G11",
            "TA-1401",
            unisoc("T606"),
            (2022, 1),
            11,
            marketing_name="Nokia G11",
        ),
        model(
            "Nokia XR20",
            "TA-1362",
            sd("480 5G"),
            (2021, 6),
            11,
            marketing_name="Nokia XR20",
        ),
    ),
)

SONY = BrandSeed(
    name="Sony",
    slug="sony",
    website_url="https://www.sony.com/electronics/smartphones",
    description=(
        "Sony Xperia smartphones. Sony firmware is identified by the XQ-xxxx "
        "model code and a customization (region) pack."
    ),
    scheme=SCHEME_SONY,
    models=(
        model(
            "Xperia 1 V",
            "XQ-DQ72",
            sd("8 Gen 2"),
            (2023, 6),
            13,
            marketing_name="Sony Xperia 1 V",
        ),
        model(
            "Xperia 5 V",
            "XQ-DE72",
            sd("8 Gen 2"),
            (2023, 9),
            13,
            marketing_name="Sony Xperia 5 V",
        ),
        model(
            "Xperia 10 V",
            "XQ-DC72",
            sd("695"),
            (2023, 6),
            13,
            marketing_name="Sony Xperia 10 V",
        ),
        model(
            "Xperia 1 IV",
            "XQ-CT72",
            sd("8 Gen 1"),
            (2022, 7),
            12,
            marketing_name="Sony Xperia 1 IV",
        ),
        model(
            "Xperia 5 IV",
            "XQ-CQ72",
            sd("8 Gen 1"),
            (2022, 10),
            12,
            marketing_name="Sony Xperia 5 IV",
        ),
        model(
            "Xperia 10 IV",
            "XQ-CC72",
            sd("695"),
            (2022, 6),
            12,
            marketing_name="Sony Xperia 10 IV",
        ),
    ),
)

BRANDS_PART_2: tuple[BrandSeed, ...] = (
    VIVO,
    REALME,
    HUAWEI,
    HONOR,
    MOTOROLA,
    NOKIA,
    SONY,
)
