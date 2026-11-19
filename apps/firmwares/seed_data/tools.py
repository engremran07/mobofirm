"""Flashing tool, tool category and flashing guide seed data.

Every tool links only to its publisher's own page. Tools that the manufacturer does
not distribute publicly (Odin, SP Flash Tool) are marked as community-distributed
and carry no download link of their own.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.firmwares.models import FlashingGuideTemplate, FlashingTool

from .chipsets import (
    CHIPSETS,
    allwinner,
    dim,
    exynos,
    helio,
    rockchip,
    sd,
    tensor,
    unisoc,
)


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
    body: str = ""


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

_APPLE_CHIPSETS = tuple(chip.key for chip in CHIPSETS if chip.vendor == "Apple")

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
        slug="lenovo-motorola-lmsa",
        name="Lenovo Rescue and Smart Assistant (LMSA)",
        category="oem-recovery-utilities",
        description=(
            "Lenovo's official Windows companion for Lenovo and Motorola phones and "
            "tablets. Its Rescue function reinstalls the manufacturer's firmware on "
            "a device that will not start; Smart Assistant backs up and transfers "
            "data. Eligibility, firmware and regional availability are decided by "
            "Lenovo and the device serial number or IMEI."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.MODERATE,
        official_url="https://support.lenovo.com/",
        features=(
            "Manufacturer rescue of a device that will not start",
            "Backup, restore and data transfer",
        ),
        requirements=(
            "A Windows computer",
            "A Lenovo or Motorola device that the tool recognises",
            "The device model, serial number or IMEI",
            "A charged device and a good USB cable",
        ),
        instructions=(
            "Back up first. Open the Rescue function, pick your exact model, and let "
            "the tool download and install the firmware Lenovo offers for it. A "
            "rescue erases the device; do not disconnect it while it runs."
        ),
        brands=("lenovo", "motorola"),
        chipsets=_QUALCOMM_CHIPSETS,
    ),
    ToolSeed(
        slug="sony-xperia-companion",
        name="Xperia Companion",
        category="oem-recovery-utilities",
        description=(
            "Sony's official computer software for Xperia phones and tablets. Its "
            "software repair function reinstalls Sony's firmware on a device that "
            "will not start and also updates and backs up devices. Availability "
            "depends on the model and region, as decided by Sony."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.SAFE,
        official_url="https://www.sony.com/electronics/support",
        features=("Software repair", "Update and backup"),
        requirements=(
            "A Windows or macOS computer",
            "A supported Xperia device",
            "A charged device and a good USB cable",
        ),
        instructions=(
            "Back up first, then use the software repair function and follow the "
            "prompts. Repair erases the device. Sony's service tool Emma is not "
            "published for end users and is not listed here."
        ),
        brands=("sony",),
        chipsets=_QUALCOMM_CHIPSETS,
    ),
    ToolSeed(
        slug="asus-flash-tool",
        name="ASUS Flash Tool",
        category="oem-recovery-utilities",
        description=(
            "ASUS's own Windows flashing utility for ASUS Zenfone and ROG Phone "
            "devices, used with the firmware package that ASUS publishes for the "
            "exact model and region. The tool and the package are listed on ASUS's "
            "support pages for supported models; check availability there."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://www.asus.com/support/",
        features=(
            "Flashes ASUS firmware packages",
            "Recovery of a device that fails to boot",
        ),
        requirements=(
            "A Windows computer",
            "ASUS USB drivers",
            "The ASUS firmware package for the exact model and region",
        ),
        instructions=(
            "Back up first. Load the firmware package for your model, connect the "
            "device in the mode the package describes and start the flash. A "
            "package for another model or region can leave the device unbootable."
        ),
        brands=("asus",),
        chipsets=_QUALCOMM_CHIPSETS,
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
    ToolSeed(
        slug="spd-researchdownload",
        name="SPD ResearchDownload and Upgrade Tool",
        category="chipset-flashing-tools",
        description=(
            "The Windows flashing utilities used with Unisoc, Spreadtrum and RDA "
            "devices: ResearchDownload for engineering and repair work and the "
            "Upgrade Tool for end-user packages. Both load a .pac firmware package. "
            "Unisoc supplies them to partners and copies are community-distributed, "
            "so verify the source and the file before use."
        ),
        tool_type=FlashingTool.ToolType.FREE,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.RISKY,
        features=(
            "Loads .pac firmware packages",
            "Flashes in the Unisoc USB download mode",
        ),
        requirements=(
            "A Windows computer",
            "Unisoc / Spreadtrum USB drivers",
            "A .pac package that matches the exact model and chipset",
        ),
        instructions=(
            "Install the USB drivers, open the tool and load the .pac file, then "
            "power the phone off and connect it in download mode. Flashing the "
            "wrong package can leave the device unable to boot."
        ),
        chipsets=(
            unisoc("T606"),
            unisoc("T610"),
            unisoc("T612"),
            unisoc("T616"),
            unisoc("T618"),
            unisoc("SC9863A"),
            unisoc("SC7731E"),
            unisoc("RDA8810PL"),
        ),
    ),
    ToolSeed(
        slug="rkdevtool",
        name="RKDevTool",
        category="chipset-flashing-tools",
        description=(
            "Rockchip's Windows flashing utility (also called Android Tool). It "
            "writes update.img packages and individual partitions to Rockchip "
            "tablets and phones in Maskrom or Loader mode. Copies are "
            "community-distributed, so verify the source before use."
        ),
        tool_type=FlashingTool.ToolType.FREE,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.RISKY,
        features=("update.img and partition flashing", "Maskrom and Loader modes"),
        requirements=(
            "A Windows computer",
            "Rockchip USB driver (DriverAssistant)",
            "An update.img that matches the exact board",
        ),
        instructions=(
            "Install the driver, put the device in Loader or Maskrom mode, open "
            "the tool and load the firmware before starting the upgrade. A wrong "
            "image can make the device unbootable."
        ),
        chipsets=(
            rockchip("RK3288"),
            rockchip("RK3326"),
            rockchip("RK3399"),
            rockchip("RK3566"),
            rockchip("RK3568"),
        ),
    ),
    ToolSeed(
        slug="phoenixsuit",
        name="PhoenixSuit",
        category="chipset-flashing-tools",
        description=(
            "Allwinner's Windows flashing utility (PhoenixSuit, with LiveSuit for "
            "Linux) for .img firmware packages on Allwinner tablets and phones over "
            "USB in FEL mode. Copies are community-distributed, so verify the "
            "source before use."
        ),
        tool_type=FlashingTool.ToolType.FREE,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.RISKY,
        features=("Flashes Allwinner .img packages", "FEL mode upgrade"),
        requirements=(
            "A Windows computer (LiveSuit on Linux)",
            "Allwinner USB driver",
            "An .img package that matches the exact board",
        ),
        instructions=(
            "Install the driver, open the tool and load the .img, then connect the "
            "device in FEL mode as described for the board. Flashing erases user "
            "data and a wrong image can brick the device."
        ),
        chipsets=(
            allwinner("A33"),
            allwinner("A64"),
            allwinner("A83T"),
            allwinner("A100"),
            allwinner("A133"),
            allwinner("A523"),
            allwinner("T507"),
        ),
    ),
    ToolSeed(
        slug="apple-finder-devices-restore",
        name="Finder / Apple Devices app",
        category="oem-recovery-utilities",
        description=(
            "Apple's own way to update or restore an iPhone or iPad from a computer. "
            "Finder does this on a Mac; the Apple Devices app does it on Windows 10 "
            "and 11. Restore installs an IPSW image and erases the device; Update "
            "keeps your data. Only IPSW files that Apple still signs can be "
            "installed. It never bypasses Activation Lock."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.MODERATE,
        official_url="https://support.apple.com/",
        features=(
            "Update or restore with the newest signed version",
            "Install a downloaded IPSW that Apple still signs",
            "Back up and restore device data",
        ),
        requirements=(
            "A Mac with Finder, or Windows 10 or 11 with the Apple Devices app",
            "A good USB cable and an unlocked device that trusts the computer",
            "An IPSW for the exact model that Apple still signs (to restore from file)",
            "The Apple Account of the device owner (Activation Lock stays in force)",
        ),
        instructions=(
            "Back up first. Connect the device, choose it in Finder or the Apple "
            "Devices app, then pick Update to keep data or Restore to erase it. To "
            "use a downloaded IPSW, Option-click (Mac) or Shift-click (Windows) the "
            "button. If the device does not respond, use recovery mode or DFU mode."
        ),
        brands=("apple",),
        chipsets=_APPLE_CHIPSETS,
        featured=True,
    ),
    ToolSeed(
        slug="apple-itunes",
        name="iTunes",
        category="oem-recovery-utilities",
        description=(
            "Apple's media app, still the way to update or restore an iPhone or iPad "
            "from Windows 8.1 and earlier Windows 10 setups that cannot use the Apple "
            "Devices app. Current Macs use Finder instead. As with Finder, only IPSW "
            "files that Apple still signs can be installed."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.WINDOWS,
        risk_level=FlashingTool.RiskLevel.MODERATE,
        official_url="https://www.apple.com/itunes/",
        features=("Update and restore", "Backup and restore"),
        requirements=(
            "An older Windows computer, or one without the Apple Devices app",
            "A good USB cable",
            "A device whose IPSW Apple still signs",
        ),
        instructions=(
            "Back up first, connect the device and open the Summary page, then use "
            "Check for Update or Restore iPhone / iPad. Restore erases the device."
        ),
        brands=("apple",),
        chipsets=_APPLE_CHIPSETS,
    ),
    ToolSeed(
        slug="apple-configurator-2",
        name="Apple Configurator 2",
        category="oem-recovery-utilities",
        description=(
            "Apple's free Mac app for deploying and servicing iPhone and iPad. Its "
            "Revive action reinstalls the system on a device that does not start "
            "while keeping data where possible, and Restore erases the device and "
            "installs a signed IPSW. Supervised and managed devices stay managed, "
            "and Activation Lock is never bypassed."
        ),
        tool_type=FlashingTool.ToolType.OEM,
        platform=FlashingTool.Platform.MAC,
        risk_level=FlashingTool.RiskLevel.MODERATE,
        official_url="https://apps.apple.com/app/apple-configurator/id1037126344",
        features=("Revive a device that does not start", "Restore with an IPSW"),
        requirements=(
            "A Mac running a supported macOS",
            "A USB cable; DFU mode for a device that does not respond",
            "An IPSW that Apple still signs",
        ),
        instructions=(
            "Connect the device, put it in DFU mode if it shows nothing, then choose "
            "Revive Device to keep data or Restore to erase it. Do not disconnect "
            "until the device restarts."
        ),
        brands=("apple",),
        chipsets=_APPLE_CHIPSETS,
    ),
    ToolSeed(
        slug="idevicerestore",
        name="idevicerestore",
        category="chipset-flashing-tools",
        description=(
            "An open-source command-line tool from the libimobiledevice project that "
            "restores or updates iOS and iPadOS devices with an IPSW. It asks Apple's "
            "signing servers for approval, so it can only install versions that Apple "
            "currently signs. It does not bypass Activation Lock or iCloud."
        ),
        tool_type=FlashingTool.ToolType.OPEN_SOURCE,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.ADVANCED,
        official_url="https://libimobiledevice.org/",
        download_url="https://github.com/libimobiledevice/idevicerestore",
        features=("Restore and update from an IPSW", "Linux, macOS and Windows builds"),
        requirements=(
            "Comfort with the command line",
            "The libimobiledevice libraries and a USB connection",
            "An IPSW for the exact model that Apple still signs",
        ),
        instructions=(
            "Back up first. Put the device in recovery or DFU mode and run "
            "idevicerestore with the IPSW. A restore erases the device, and a wrong "
            "or unsigned image is refused."
        ),
        brands=("apple",),
        chipsets=_APPLE_CHIPSETS,
    ),
    ToolSeed(
        slug="futurerestore",
        name="futurerestore",
        category="chipset-flashing-tools",
        description=(
            "An open-source command-line tool that restores a device with SHSH blobs "
            "saved earlier for a version that Apple has since stopped signing. It "
            "works only when valid blobs exist for that exact device and version, "
            "and only for versions and devices where the method is still possible; "
            "it is not a general downgrade tool. It does not bypass Activation "
            "Lock or iCloud."
        ),
        tool_type=FlashingTool.ToolType.OPEN_SOURCE,
        platform=FlashingTool.Platform.MULTI,
        risk_level=FlashingTool.RiskLevel.RISKY,
        official_url="https://github.com/futurerestore/futurerestore",
        features=("Restore with saved SHSH blobs", "Command-line tool"),
        requirements=(
            "Valid SHSH blobs for the exact device (ECID) and version",
            "A currently signed IPSW for the required baseband and firmware parts",
            "Command-line experience; mistakes can leave the device unbootable",
        ),
        instructions=(
            "Use only blobs saved for your own device. Back up first; a failed "
            "restore may need DFU mode and a normal signed restore to recover."
        ),
        brands=("apple",),
        chipsets=_APPLE_CHIPSETS,
    ),
)

GUIDE_PLATFORM_TOOLS = ("android-sdk-platform-tools",)
GUIDE_XIAOMI_TOOLS = ("miflash-tool", "mi-unlock-tool", "android-sdk-platform-tools")

STOCK = FlashingGuideTemplate.GuideType.STOCK_FLASH
UNLOCK = FlashingGuideTemplate.GuideType.UNLOCK_BOOTLOADER
RECOVERY = FlashingGuideTemplate.GuideType.RECOVERY
REPAIR = FlashingGuideTemplate.GuideType.REPAIR
GENERAL = FlashingGuideTemplate.GuideType.GENERAL
DOWNGRADE = FlashingGuideTemplate.GuideType.DOWNGRADE

APPLE_GUIDE_BODY = """{intro}

## Before you start

- Back up the device first. A restore erases it.
- Use the exact IPSW for your model; Apple signs only some versions at a time.
- You need the Apple Account and passcode of the device owner. This guide does not
  remove Activation Lock or an iCloud account, and we do not cover bypassing them.
- Use a good USB cable and keep the device connected until it restarts.

## Tools

{tools}

## Steps

{steps}

## If something goes wrong

Do not repeat a failed restore blindly. Check that the IPSW matches the model and
is still signed by Apple, try another cable or port, and contact Apple or an
authorised service provider if the device still does not start.
"""

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
    GuideSeed(
        STOCK,
        "apple",
        "How to restore {brand} iPhone and iPad firmware with Finder or the Apple "
        "Devices app",
        "Apple firmware is an IPSW image. Finder on a Mac and the Apple Devices app "
        "on Windows install the newest signed version, or an IPSW file you choose. "
        "Only IPSW files that Apple still signs for your exact model will install.",
        ("apple-finder-devices-restore", "apple-itunes"),
        body=APPLE_GUIDE_BODY,
    ),
    GuideSeed(
        RECOVERY,
        "apple",
        "How to use recovery mode on {brand} iPhone and iPad",
        "Recovery mode lets Finder or the Apple Devices app update or restore an "
        "iPhone or iPad that will not start normally. Try Update first: it keeps "
        "your data when it works.",
        ("apple-finder-devices-restore", "apple-itunes"),
        body=APPLE_GUIDE_BODY,
    ),
    GuideSeed(
        REPAIR,
        "apple",
        "How to use DFU mode on {brand} iPhone and iPad, by device generation",
        "DFU mode is the lowest-level state a device can be restored from. The key "
        "sequence depends on the device generation, and a wrong sequence only "
        "restarts the device. Use DFU only when recovery mode does not work.",
        ("apple-finder-devices-restore", "apple-configurator-2", "idevicerestore"),
        body=APPLE_GUIDE_BODY,
    ),
    GuideSeed(
        REPAIR,
        "apple",
        "How to revive or restore {brand} devices with Apple Configurator 2",
        "Apple Configurator 2 on a Mac can revive an iPhone or iPad that does not "
        "start, reinstalling the system while keeping data where possible, or "
        "restore it with an IPSW that Apple still signs.",
        ("apple-configurator-2", "apple-finder-devices-restore"),
        body=APPLE_GUIDE_BODY,
    ),
    GuideSeed(
        GENERAL,
        "apple",
        "{brand} OTA update or IPSW restore: which one to use",
        "An over-the-air (OTA) update changes only what is needed and keeps your "
        "data. An IPSW restore installs a complete firmware image from a computer. "
        "Apple decides which versions are signed, so older IPSW files stop working.",
        ("apple-finder-devices-restore", "apple-configurator-2"),
        body=APPLE_GUIDE_BODY,
    ),
    GuideSeed(
        DOWNGRADE,
        "apple",
        "Can you downgrade iOS or iPadOS on {brand} devices? Signed IPSW rules",
        "Apple installs only IPSW files it still signs, so a downgrade is normally "
        "possible only to a version Apple is still signing. futurerestore needs "
        "SHSH blobs saved earlier for the exact device and version and works only "
        "in narrow cases.",
        ("apple-finder-devices-restore", "idevicerestore", "futurerestore"),
        body=APPLE_GUIDE_BODY,
    ),
)
