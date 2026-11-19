"""Apple catalogue: iPhone and iPad models and their A-series and M-series chips.

Apple devices are listed for their chipset, restore tools and guides only. No
firmware is seeded: only IPSW files that Apple still signs can be installed, and
the signed set changes without notice, so listings come from reviewed uploads.
"""

from __future__ import annotations

from .chipsets import apple
from .types import SCHEME_APPLE, BrandSeed, model

APPLE = BrandSeed(
    name="Apple",
    slug="apple",
    website_url="https://www.apple.com/",
    description=(
        "Apple iPhone and iPad. Firmware is an IPSW restore image installed with "
        "Finder, the Apple Devices app or Apple Configurator 2, or an Apple OTA "
        "update. Only IPSW files that Apple still signs can be installed, and "
        "Activation Lock is never bypassed."
    ),
    scheme=SCHEME_APPLE,
    models=(
        model(
            "iPhone XS", "A1920", apple("A12 Bionic"), (2018, 9), 0, os_label="iOS 12"
        ),
        model(
            "iPhone 11", "A2111", apple("A13 Bionic"), (2019, 9), 0, os_label="iOS 13"
        ),
        model(
            "iPhone 12", "A2172", apple("A14 Bionic"), (2020, 10), 0, os_label="iOS 14"
        ),
        model(
            "iPhone 13", "A2633", apple("A15 Bionic"), (2021, 9), 0, os_label="iOS 15"
        ),
        model(
            "iPhone 14", "A2882", apple("A15 Bionic"), (2022, 9), 0, os_label="iOS 16"
        ),
        model(
            "iPhone 15 Pro", "A2848", apple("A17 Pro"), (2023, 9), 0, os_label="iOS 17"
        ),
        model("iPhone 16", "A3081", apple("A18"), (2024, 9), 0, os_label="iOS 18"),
        model(
            "iPad (10th generation)",
            "A2696",
            apple("A14 Bionic"),
            (2022, 10),
            0,
            os_label="iPadOS 16",
        ),
        model(
            "iPad Air 11-inch (M2)",
            "A2902",
            apple("M2"),
            (2024, 5),
            0,
            os_label="iPadOS 17",
        ),
        model(
            "iPad Pro 11-inch (M4)",
            "A2836",
            apple("M4"),
            (2024, 5),
            0,
            os_label="iPadOS 17",
        ),
    ),
)
