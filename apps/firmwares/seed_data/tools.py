"""Flashing tool, tool category and flashing guide seed data.

Every tool links only to its publisher's own page. Tools that the manufacturer does
not distribute publicly (Odin, SP Flash Tool) are marked as community-distributed
and carry no download link of their own.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.firmwares.models import FlashingGuideTemplate, FlashingTool

from .chipsets import dim, exynos, helio, sd, tensor


@dataclass(frozen=True)
class ToolCategorySeed:
    slug: str
    name: str
    description: str
    icon: str
    sort_order: int


@dataclass(frozen=True)
class ToolSeed:
    slug: str
    name: str
    category: str
    description: str
    tool_type: str
    platform: str
    risk_level: str
    official_url: str = ""
    download_url: str = ""
    features: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    instructions: str = ""
    brands: tuple[str, ...] = ()
    chipsets: tuple[str, ...] = ()
    featured: bool = False


@dataclass(frozen=True)
class GuideSeed:
    """One flashing guide generated through the guide service for a brand or model."""

    guide_type: str
    brand: str
    title: str
    intro: str
    tools: tuple[str, ...]
    model_name: str = ""
    chipset_filter: str = ""


TOOL_CATEGORIES: tuple[ToolCategorySeed, ...] = (
    ToolCategorySeed(
        "android-platform-tools",
        "Android platform tools",
        "Official Android developer tools and supported device workflows.",
        "smartphone",
        1,
    ),
    ToolCategorySeed(
        "oem-recovery-utilities",
        "OEM recovery utilities",
        "Manufacturer-provided recovery utilities for supported devices.",
        "shield-check",
        2,
    ),
    ToolCategorySeed(
        "chipset-flashing-tools",
        "Chipset flashing tools",
        "Tools tied to a chipset family or distributed by the community rather than "
        "by the device manufacturer. Check the publisher and verify every file "
        "before running it.",
        "cpu",
        3,
    ),
)

_FASTBOOT_BRANDS = (
    "google",
    "oneplus",
    "xiaomi",
    "redmi",
    "poco",
    "motorola",
    "sony",
    "asus",
    "nothing",
    "fairphone",
    "nokia",
    "realme",
    "oppo",
    "lenovo",
    "zte",
)

_MEDIATEK_CHIPSETS = (
    dim("9300"),
    dim("9200 Plus"),
    dim("9200"),
    dim("9000"),
    dim("8300 Ultra"),
    dim("8100 Ultra"),
    dim("8020"),
    dim("7350 Pro"),
    dim("7300"),
    dim("7200 Pro"),
    dim("7200 Ultra"),
    dim("7200"),
    dim("7050"),
    dim("6080"),
    dim("6020"),
    dim("930"),
    helio("G99"),
    helio("G99 Ultra"),
    helio("G88"),
    helio("G85"),
    helio("G70"),
    helio("G37"),
    helio("G36"),
    helio("G35"),
    helio("A22"),
)

_QUALCOMM_CHIPSETS = (
    sd("8 Gen 3 for Galaxy"),
    sd("8 Gen 3"),
    sd("8 Gen 2"),
    sd("8 Plus Gen 1"),
    sd("8 Gen 1"),
    sd("8s Gen 3"),
    sd("7 Plus Gen 2"),
    sd("7 Gen 3"),
    sd("7s Gen 3"),
    sd("7s Gen 2"),
    sd("7 Gen 1"),
    sd("778G"),
    sd("778G Plus"),
    sd("695"),
    sd("680"),
    sd("4 Gen 1"),
    sd("480 Plus 5G"),
    sd("480 5G"),
    sd("888"),
    sd("870"),
    sd("855"),
    sd("750G"),
    sd("632"),
)

TOOLS: tuple[ToolSeed, ...] = (
    ToolSeed(
        slug="android-sdk-platform-tools",
        name="Android SDK Platform-Tools",
        category="android-platform-tools",
        description=(
            "Google's official command-line package containing ADB and Fastboot. "
            "It is the standard way to talk to an Android device in fastboot mode "
            "and is required by most manufacturer-supported flashing workflows."
        ),
        tool_type=FlashingTool.ToolType.OPEN_SOURCE,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.MODERATE,
        official_url="https://developer.android.com/tools/releases/platform-tools",
        download_url="https://developer.android.com/tools/releases/platform-tools",
        features=(
            "ADB device communication",
            "Fastboot flashing of partition images",
            "Bootloader unlock and lock commands on supporting devices",
        ),
        requirements=(
            "A computer with a USB port",
            "USB debugging enabled on the phone",
        ),
        instructions=(
            "Download the package from Google, extract it, open a terminal in the "
            "folder and run `adb devices` to confirm the phone is detected before "
            "using any `fastboot` command."
        ),
        brands=_FASTBOOT_BRANDS,
        featured=True,
    ),
    ToolSeed(
        slug="android-flash-tool",
        name="Android Flash Tool",
        category="android-platform-tools",
        description=(
            "Google's browser-based installer for Pixel devices. It flashes official "
            "factory builds over WebUSB and can unlock the bootloader when the "
            "device allows it."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://flash.android.com/",
        features=("Official Pixel factory builds", "Compatibility checks"),
        requirements=("A supported Pixel phone", "A Chromium-based browser"),
        instructions=(
            "Open the site in a Chromium-based browser, connect the phone and "
            "follow the on-screen prompts. Flashing erases device data."
        ),
        brands=("google",),
        chipsets=(tensor(), tensor("G2"), tensor("G3"), tensor("G4")),
        featured=True,
    ),
    ToolSeed(
        slug="motorola-software-fix",
        name="Motorola Software Fix",
        category="oem-recovery-utilities",
        description=(
            "Motorola's official recovery utility for supported Motorola phones. "
            "Check regional availability and device eligibility with Motorola."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://en-us.support.motorola.com/app/softwarefix",
        download_url="https://en-us.support.motorola.com/app/softwarefix",
        features=("Manufacturer-supported software recovery",),
        requirements=("A supported Motorola phone", "A Windows computer"),
        brands=("motorola",),
    ),
    ToolSeed(
        slug="samsung-smart-switch",
        name="Samsung Smart Switch",
        category="oem-recovery-utilities",
        description=(
            "Samsung's own app for backing up and restoring Galaxy devices. Back "
            "up with it before flashing any firmware."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.SAFE,
        official_url="https://www.samsung.com/us/support/owners/app/smart-switch",
        features=("Backup and restore", "Data transfer between Galaxy devices"),
        requirements=("A Samsung Galaxy device", "A Windows or macOS computer"),
        brands=("samsung",),
    ),
    ToolSeed(
        slug="odin",
        name="Odin",
        category="chipset-flashing-tools",
        description=(
            "Samsung's firmware flashing utility for Windows. Samsung does not "
            "publish it for the public, so copies are community-distributed: check "
            "the source and verify the file before running it."
        ),
        tool_type=FlashingTool.ToolType.FREE,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        features=("AP, BL, CP and CSC package slots", "Download mode flashing"),
        requirements=("A Windows computer", "Samsung USB drivers"),
        instructions=(
            "Boot the phone into Download mode, load the AP, BL, CP and CSC files "
            "into their slots and press Start. Use HOME_CSC instead of CSC to keep "
            "user data."
        ),
        brands=("samsung",),
        chipsets=(exynos("2400"), exynos("2200"), exynos("1380")),
        featured=True,
    ),
    ToolSeed(
        slug="heimdall",
        name="Heimdall",
        category="chipset-flashing-tools",
        description=(
            "An open-source, cross-platform alternative to Odin that speaks the "
            "same protocol to Samsung devices in Download mode."
        ),
        tool_type=FlashingTool.ToolType.OPEN_SOURCE,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://glassechidna.com.au/heimdall/",
        features=("Runs on Windows, macOS and Linux", "Flashes individual partitions"),
        requirements=("libusb", "A Samsung device in Download mode"),
        brands=("samsung",),
        chipsets=(exynos("2400"), exynos("2200"), exynos("1380")),
    ),
    ToolSeed(
        slug="miflash-tool",
        name="MiFlash Tool",
        category="oem-recovery-utilities",
        description=(
            "Xiaomi's flashing utility for fastboot ROM packages on Xiaomi, Redmi "
            "and POCO phones. Obtain it from Xiaomi's support channels and match "
            "the package to your exact model."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://www.mi.com/global/support/",
        features=(
            "Fastboot ROM flashing",
            "Clean-all, save-user-data and lock options",
        ),
        requirements=("A Windows computer", "An unlocked bootloader"),
        brands=("xiaomi", "redmi", "poco"),
    ),
    ToolSeed(
        slug="mi-unlock-tool",
        name="Mi Unlock Tool",
        category="oem-recovery-utilities",
        description=(
            "Xiaomi's official bootloader unlock tool. It requires a Xiaomi account "
            "bound to the phone and a waiting period before the bootloader can be "
            "unlocked."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://en.miui.com/unlock/",
        features=("Official bootloader unlock", "Account-bound unlock approval"),
        requirements=("A Windows computer", "A Xiaomi account bound to the phone"),
        brands=("xiaomi", "redmi", "poco"),
    ),
    ToolSeed(
        slug="sp-flash-tool",
        name="SP Flash Tool",
        category="chipset-flashing-tools",
        description=(
            "The scatter-file based flashing utility for MediaTek devices. "
            "MediaTek supplies it to partners and copies are community-distributed, "
            "so verify the source and the file before use."
        ),
        tool_type=FlashingTool.ToolType.FREE,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.RISKY,
        official_url="https://spflashtool.com/",
        features=("Scatter-file flashing", "Download Only and Firmware Upgrade modes"),
        requirements=("MediaTek USB VCOM drivers", "A matching scatter file"),
        chipsets=_MEDIATEK_CHIPSETS,
        featured=True,
    ),
    ToolSeed(
        slug="qpst-qfil",
        name="QPST and QFIL",
        category="chipset-flashing-tools",
        description=(
            "Qualcomm's Product Support Tools and Flash Image Loader for Emergency "
            "Download (EDL) mode. Qualcomm distributes them through its package "
            "manager to registered users."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.RISKY,
        official_url="https://qpm.qualcomm.com/",
        features=("EDL mode flashing", "Flat build and meta build support"),
        requirements=(
            "Qualcomm HS-USB QDLoader 9008 drivers",
            "A matching firehose programmer",
        ),
        chipsets=_QUALCOMM_CHIPSETS,
    ),
    ToolSeed(
        slug="huawei-hisuite",
        name="Huawei HiSuite",
        category="oem-recovery-utilities",
        description=(
            "Huawei's official companion software for backup, restore and software "
            "updates on Huawei phones and tablets."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.SAFE,
        official_url="https://consumer.huawei.com/en/support/hisuite/",
        features=("Backup and restore", "Software update and recovery"),
        requirements=("A Windows computer", "A supported Huawei device"),
        brands=("huawei",),
    ),
)

GUIDE_PLATFORM_TOOLS = ("android-sdk-platform-tools",)
GUIDE_XIAOMI_TOOLS = ("miflash-tool", "mi-unlock-tool", "android-sdk-platform-tools")

STOCK = FlashingGuideTemplate.GuideType.STOCK_FLASH
UNLOCK = FlashingGuideTemplate.GuideType.UNLOCK_BOOTLOADER

GUIDES: tuple[GuideSeed, ...] = (
    GuideSeed(
        STOCK,
        "samsung",
        "How to flash stock firmware on {brand} {model}",
        "Samsung firmware is flashed in Download mode with Odin on Windows or "
        "Heimdall on Linux and macOS. A package contains AP, BL, CP and CSC files; "
        "choosing HOME_CSC instead of CSC keeps your data. Always match the full "
        "model code and the region of the package.",
        ("odin", "heimdall", "samsung-smart-switch"),
    ),
    GuideSeed(
        STOCK,
        "xiaomi",
        "How to flash stock firmware on {brand} {model}",
        "Xiaomi publishes fastboot ROM packages per model and region. They are "
        "flashed with the MiFlash Tool while the phone is in fastboot mode "
        "(Volume Down plus Power), and the bootloader normally has to be unlocked "
        "first.",
        GUIDE_XIAOMI_TOOLS,
    ),
    GuideSeed(
        STOCK,
        "redmi",
        "How to flash stock firmware on {brand} {model}",
        "Redmi phones use the same fastboot ROM packages as Xiaomi. Flash them "
        "with the MiFlash Tool in fastboot mode and match the package to the exact "
        "model code and codename.",
        GUIDE_XIAOMI_TOOLS,
    ),
    GuideSeed(
        STOCK,
        "poco",
        "How to flash stock firmware on {brand} {model}",
        "POCO phones use the same fastboot ROM packages as Xiaomi. Flash them with "
        "the MiFlash Tool in fastboot mode and match the package to the exact "
        "model code and codename.",
        GUIDE_XIAOMI_TOOLS,
    ),
    GuideSeed(
        STOCK,
        "google",
        "How to flash stock firmware on {brand} {model}",
        "Google publishes factory images for Pixel phones. The Android Flash Tool "
        "installs them from a Chromium-based browser, or you can flash the image "
        "manually with Fastboot. The bootloader must be unlocked, which erases "
        "the device.",
        ("android-flash-tool", "android-sdk-platform-tools"),
    ),
    GuideSeed(
        STOCK,
        "motorola",
        "How to flash stock firmware on {brand} {model}",
        "Motorola's Software Fix tool restores supported phones to their stock "
        "software. For manual flashing, Motorola firmware packages are applied "
        "with Fastboot, and the exact XT model code and region must match.",
        ("motorola-software-fix", "android-sdk-platform-tools"),
    ),
    GuideSeed(
        STOCK,
        "nothing",
        "How to flash stock firmware on {brand} {model}",
        "Nothing publishes full OTA packages and fastboot images for its phones. "
        "Flash images with Fastboot after unlocking the bootloader and match the "
        "package to your exact model code.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        STOCK,
        "huawei",
        "How to flash stock firmware on {brand} {model}",
        "Huawei devices of the Android and EMUI era are restored with HiSuite or "
        "the phone's eRecovery. Huawei stopped issuing bootloader unlock codes in "
        "2018, so custom flashing is not an option on these phones.",
        ("huawei-hisuite",),
    ),
    GuideSeed(
        STOCK,
        "fairphone",
        "How to flash stock firmware on {brand} {model}",
        "Fairphone publishes factory images and OTA updates. Flash a factory image "
        "with Fastboot after unlocking the bootloader, and match the image to the "
        "exact device generation.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        UNLOCK,
        "xiaomi",
        "How to unlock the bootloader on {brand} {model}",
        "Xiaomi unlocks the bootloader with the Mi Unlock Tool. The phone must be "
        "bound to your Xiaomi account and a waiting period applies before "
        "approval. Unlocking erases all data.",
        ("mi-unlock-tool", "android-sdk-platform-tools"),
    ),
    GuideSeed(
        UNLOCK,
        "google",
        "How to unlock the bootloader on {brand} {model}",
        "Pixel phones unlock with Fastboot once OEM unlocking is enabled in "
        "Developer options. Carrier-locked models may not allow it. Unlocking "
        "erases all data.",
        ("android-sdk-platform-tools", "android-flash-tool"),
    ),
    GuideSeed(
        UNLOCK,
        "oneplus",
        "How to unlock the bootloader on {brand} {model}",
        "OnePlus phones unlock with Fastboot once OEM unlocking is enabled in "
        "Developer options. Unlocking erases all data and some carrier variants "
        "do not allow it.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        UNLOCK,
        "motorola",
        "How to unlock the bootloader on {brand} {model}",
        "Motorola issues an unlock key through its website for eligible phones; "
        "the key is then applied with Fastboot. Carrier-locked models may be "
        "excluded, and unlocking erases all data.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        UNLOCK,
        "nothing",
        "How to unlock the bootloader on {brand} {model}",
        "Nothing phones unlock with Fastboot once OEM unlocking is enabled in "
        "Developer options. Unlocking erases all data and can affect some apps "
        "that rely on device integrity checks.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        UNLOCK,
        "sony",
        "How to unlock the bootloader on {brand} {model}",
        "Sony provides bootloader unlock keys for supported Xperia models through "
        "its developer site; some regional and carrier models are excluded. "
        "Unlocking erases data and can disable certain camera features.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        UNLOCK,
        "fairphone",
        "How to unlock the bootloader on {brand} {model}",
        "Fairphone supports bootloader unlocking and documents the procedure. "
        "Use Fastboot once OEM unlocking is enabled; unlocking erases all data.",
        GUIDE_PLATFORM_TOOLS,
    ),
    GuideSeed(
        STOCK,
        "xiaomi",
        "How to flash {brand} {model} firmware on a Snapdragon device ({chipset})",
        "Qualcomm Snapdragon devices expose Fastboot for normal flashing and "
        "Emergency Download (EDL) mode for deeper recovery with QFIL. Use EDL only "
        "with the firmware package and firehose programmer made for the device.",
        ("qpst-qfil", "android-sdk-platform-tools"),
        model_name="13",
        chipset_filter="Snapdragon",
    ),
    GuideSeed(
        STOCK,
        "infinix",
        "How to flash {brand} {model} firmware on a MediaTek device ({chipset})",
        "MediaTek devices are flashed with SP Flash Tool using the scatter file "
        "that ships in the firmware package. Choose Download Only to keep data and "
        "Firmware Upgrade only when a full rewrite is required.",
        ("sp-flash-tool",),
        model_name="Note 40 Pro",
        chipset_filter="Helio",
    ),
    GuideSeed(
        STOCK,
        "samsung",
        "How to flash {brand} {model} firmware on an Exynos device ({chipset})",
        "Exynos-based Galaxy phones are flashed in Download mode with Odin or "
        "Heimdall using the AP, BL, CP and CSC files of a package that matches "
        "the model code.",
        ("odin", "heimdall"),
        model_name="Galaxy S24",
        chipset_filter="Exynos",
    ),
    GuideSeed(
        STOCK,
        "google",
        "How to flash {brand} {model} firmware on a Tensor device ({chipset})",
        "Google Tensor devices are restored with Google's factory images through "
        "the Android Flash Tool or Fastboot. The bootloader must be unlocked first.",
        ("android-flash-tool", "android-sdk-platform-tools"),
        model_name="Pixel 8",
        chipset_filter="Tensor",
    ),
)
