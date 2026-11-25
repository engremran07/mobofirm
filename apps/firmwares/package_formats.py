"""Firmware package formats by chipset vendor: the single source of truth.

A firmware package is laid out for one flashing workflow, and that workflow is fixed
by the chipset vendor of the device (SP Flash Tool scatter packages for MediaTek,
QFIL for Qualcomm, Odin for Samsung Exynos, ...). This module owns

* :class:`FirmwarePackageFormat`, the stored value of ``package_format``;
* which formats each supported vendor may use, plus a default;
* the formats that are generic to every chipset;
* the flashing tool (seeded ``FlashingTool`` slug and name) matching each format.

The model validation, the upload form, the REST serializer, the admin filter, the
firmware pages and the automated blog guides all read from here. It imports no model
so ``apps.firmwares.models`` can depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import models

from .chipsets import (
    ALLWINNER,
    APPLE,
    GOOGLE,
    HISILICON,
    MEDIATEK,
    QUALCOMM,
    ROCKCHIP,
    SAMSUNG,
    UNISOC,
    canonical_vendor,
    vendor_for,
)


class FirmwarePackageFormat(models.TextChoices):
    UNKNOWN = "unknown", "Not specified"
    RECOVERY_ROM = "recovery_rom", "Recovery ROM"
    FASTBOOT_ROM = "fastboot_rom", "Fastboot ROM"
    FULL_OTA = "full_ota", "Full OTA"
    INCREMENTAL_OTA = "incremental_ota", "Incremental OTA"
    FACTORY_IMAGE = "factory_image", "Factory image"
    SERVICE_PACKAGE = "service_package", "Service / Engineering package"
    PARTITION_IMAGE = "partition_image", "Partition image"
    OTHER = "other", "Other package format"
    SCATTER_FIRMWARE = "scatter_firmware", "Scatter firmware (SP Flash Tool, MediaTek)"
    MTK_DOWNLOAD_AGENT = (
        "mtk_download_agent",
        "Download agent package (MediaTek)",
    )
    QFIL_PACKAGE = "qfil_package", "QFIL package (Qualcomm, EDL 9008)"
    ODIN_PACKAGE = "odin_package", "Odin package (Samsung AP/BL/CP/CSC)"
    PAC_PACKAGE = "pac_package", "PAC package (Unisoc / Spreadtrum / RDA)"
    ROCKCHIP_UPDATE_IMG = "rockchip_update_img", "update.img package (Rockchip)"
    ALLWINNER_IMG = "allwinner_img", "PhoenixSuit / LiveSuit .img (Allwinner)"
    KIRIN_UPDATE_APP = "kirin_update_app", "UPDATE.APP package (HiSilicon Kirin)"
    APPLE_IPSW = "apple_ipsw", "IPSW restore image (Apple)"
    APPLE_OTA = "apple_ota", "Apple OTA / delta update (Apple)"


F = FirmwarePackageFormat

# Formats every chipset may use, in dropdown order.
GENERIC_FORMATS: tuple[str, ...] = (
    F.RECOVERY_ROM,
    F.FASTBOOT_ROM,
    F.FULL_OTA,
    F.INCREMENTAL_OTA,
    F.FACTORY_IMAGE,
    F.PARTITION_IMAGE,
    F.OTHER,
)
# Always accepted: "not specified", and the service / engineering package used by
# service tools of any chipset (it also keeps previously stored rows valid).
NEUTRAL_FORMATS: tuple[str, ...] = (F.UNKNOWN, F.SERVICE_PACKAGE)

# The brand whose devices are all flashed with Odin whatever SoC they carry
# (Galaxy phones with a Snapdragon chip use the same AP/BL/CP/CSC packages).
ODIN_BRAND_SLUGS: frozenset[str] = frozenset({"samsung"})
# Every Apple device carries an Apple SoC, so the brand alone decides the vendor.
APPLE_BRAND_SLUGS: frozenset[str] = frozenset({"apple"})


@dataclass(frozen=True)
class FlashTool:
    """A seeded ``FlashingTool`` (by slug) with its display name."""

    slug: str
    name: str


SP_FLASH_TOOL = FlashTool("sp-flash-tool", "SP Flash Tool")
QFIL = FlashTool("qpst-qfil", "QPST and QFIL")
ODIN = FlashTool("odin", "Odin")
SPD_TOOL = FlashTool("spd-researchdownload", "SPD ResearchDownload and Upgrade Tool")
RKDEVTOOL = FlashTool("rkdevtool", "RKDevTool")
PHOENIXSUIT = FlashTool("phoenixsuit", "PhoenixSuit")
HISUITE = FlashTool("huawei-hisuite", "Huawei HiSuite")
PLATFORM_TOOLS = FlashTool("android-sdk-platform-tools", "Android SDK Platform-Tools")
ANDROID_FLASH_TOOL = FlashTool("android-flash-tool", "Android Flash Tool")
FINDER_RESTORE = FlashTool("apple-finder-devices-restore", "Finder / Apple Devices app")
ITUNES = FlashTool("apple-itunes", "iTunes")
APPLE_CONFIGURATOR = FlashTool("apple-configurator-2", "Apple Configurator 2")
IDEVICERESTORE = FlashTool("idevicerestore", "idevicerestore")
FUTURERESTORE = FlashTool("futurerestore", "futurerestore")

# Apple's restore tools, most common first. Only Apple devices use them.
APPLE_TOOLS: tuple[FlashTool, ...] = (
    FINDER_RESTORE,
    APPLE_CONFIGURATOR,
    ITUNES,
    IDEVICERESTORE,
    FUTURERESTORE,
)

# Package format -> the tool that flashes it. Formats without an entry are applied
# with the platform tools (fastboot / adb sideload) or have no dedicated tool.
FORMAT_TOOLS: dict[str, FlashTool] = {
    F.APPLE_IPSW: FINDER_RESTORE,
    F.APPLE_OTA: FINDER_RESTORE,
    F.SCATTER_FIRMWARE: SP_FLASH_TOOL,
    F.MTK_DOWNLOAD_AGENT: SP_FLASH_TOOL,
    F.QFIL_PACKAGE: QFIL,
    F.ODIN_PACKAGE: ODIN,
    F.PAC_PACKAGE: SPD_TOOL,
    F.ROCKCHIP_UPDATE_IMG: RKDEVTOOL,
    F.ALLWINNER_IMG: PHOENIXSUIT,
    F.KIRIN_UPDATE_APP: HISUITE,
    F.FASTBOOT_ROM: PLATFORM_TOOLS,
    F.PARTITION_IMAGE: PLATFORM_TOOLS,
    F.RECOVERY_ROM: PLATFORM_TOOLS,
    F.FULL_OTA: PLATFORM_TOOLS,
    F.INCREMENTAL_OTA: PLATFORM_TOOLS,
}


@dataclass(frozen=True)
class VendorProfile:
    """The package formats of one chipset vendor."""

    vendor: str
    specific: tuple[str, ...]
    default: str
    summary: str


VENDOR_PROFILES: dict[str, VendorProfile] = {
    profile.vendor: profile
    for profile in (
        VendorProfile(
            MEDIATEK,
            (F.SCATTER_FIRMWARE, F.MTK_DOWNLOAD_AGENT),
            F.SCATTER_FIRMWARE,
            "SP Flash Tool scatter package: an *_Android_scatter.txt file plus the "
            "partition images, or a MediaTek download agent (DA) package.",
        ),
        VendorProfile(
            QUALCOMM,
            (F.QFIL_PACKAGE, F.FASTBOOT_ROM),
            F.QFIL_PACKAGE,
            "QFIL package: rawprogram and patch XML files with a firehose programmer "
            "for EDL 9008 mode, or a fastboot ROM.",
        ),
        VendorProfile(
            SAMSUNG,
            (F.ODIN_PACKAGE,),
            F.ODIN_PACKAGE,
            "Odin package: AP, BL, CP and CSC .tar.md5 files flashed in Download mode.",
        ),
        VendorProfile(
            UNISOC,
            (F.PAC_PACKAGE,),
            F.PAC_PACKAGE,
            "PAC package flashed with ResearchDownload or the SPD Upgrade Tool "
            "(Unisoc, Spreadtrum and RDA parts).",
        ),
        VendorProfile(
            ROCKCHIP,
            (F.ROCKCHIP_UPDATE_IMG,),
            F.ROCKCHIP_UPDATE_IMG,
            "update.img package flashed with RKDevTool (or the Rockchip Android "
            "tool) in Maskrom or Loader mode.",
        ),
        VendorProfile(
            ALLWINNER,
            (F.ALLWINNER_IMG,),
            F.ALLWINNER_IMG,
            "PhoenixSuit or LiveSuit .img package flashed over USB in FEL mode.",
        ),
        VendorProfile(
            HISILICON,
            (F.KIRIN_UPDATE_APP,),
            F.KIRIN_UPDATE_APP,
            "UPDATE.APP package applied through HiSuite or the eRecovery mode.",
        ),
        VendorProfile(
            APPLE,
            (F.APPLE_IPSW, F.APPLE_OTA),
            F.APPLE_IPSW,
            "IPSW restore image installed with Finder, the Apple Devices app or "
            "Apple Configurator 2, or an Apple OTA / delta update. Only IPSW files "
            "Apple still signs can be installed.",
        ),
        VendorProfile(
            GOOGLE,
            (F.FACTORY_IMAGE, F.FULL_OTA),
            F.FACTORY_IMAGE,
            "Factory image (fastboot zip) flashed with Fastboot or the Android "
            "Flash Tool, or a full or incremental OTA.",
        ),
    )
}


def _effective_vendor(vendor: str, brand_slug: str) -> str:
    """Canonical vendor; Apple devices are known by their brand alone."""
    canonical = canonical_vendor(vendor)
    if not canonical and brand_slug in APPLE_BRAND_SLUGS:
        return APPLE
    return canonical


def vendor_of_chipset(chipset: Any) -> str:
    """Canonical vendor of a ``Chipset`` row (or ``""``)."""
    value = getattr(chipset, "vendor", "") if chipset else ""
    return canonical_vendor(getattr(value, "name", value))


def vendor_for_device(
    *, variant: Any = None, model: Any = None, chipset_text: str = ""
) -> str:
    """The chipset vendor that decides the package format.

    Order: the variant's chipset, the model's chipset, then free chipset text. Each
    uses the resolved ``Chipset`` row first and falls back to its text.
    """
    for device in (variant, model):
        if device is None:
            continue
        vendor = vendor_of_chipset(getattr(device, "chipset_ref", None))
        if vendor:
            return vendor
        vendor = vendor_for(getattr(device, "chipset", ""))
        if vendor:
            return vendor
    return vendor_for(chipset_text)


def allowed_formats(vendor: str = "", brand_slug: str = "") -> tuple[str, ...]:
    """Allowed format values for a vendor, vendor-specific ones first.

    An empty or unsupported vendor (chipset not known) allows every format, since
    nothing can be proven wrong.
    """
    canonical = _effective_vendor(vendor, brand_slug)
    if not canonical:
        return tuple(FirmwarePackageFormat.values)
    profile = VENDOR_PROFILES.get(canonical)
    specific = list(profile.specific) if profile else []
    if brand_slug in ODIN_BRAND_SLUGS and F.ODIN_PACKAGE not in specific:
        specific.insert(0, F.ODIN_PACKAGE)
    ordered = [fmt for fmt in specific if fmt not in GENERIC_FORMATS]
    ordered += [fmt for fmt in specific if fmt in GENERIC_FORMATS]
    ordered += [fmt for fmt in GENERIC_FORMATS if fmt not in ordered]
    ordered += [fmt for fmt in NEUTRAL_FORMATS if fmt not in ordered]
    return tuple(ordered)


def default_format(vendor: str = "", brand_slug: str = "") -> str:
    """The format preselected for a vendor (``unknown`` when it has no profile)."""
    if brand_slug in ODIN_BRAND_SLUGS:
        return F.ODIN_PACKAGE
    profile = VENDOR_PROFILES.get(_effective_vendor(vendor, brand_slug))
    return profile.default if profile else F.UNKNOWN


def format_choices(vendor: str = "", brand_slug: str = "") -> list[tuple[str, str]]:
    """``(value, label)`` choices for a dropdown; "Not specified" comes first."""
    labels = dict(FirmwarePackageFormat.choices)
    values = allowed_formats(vendor, brand_slug)
    ordered = [F.UNKNOWN, *(value for value in values if value != F.UNKNOWN)]
    return [(value, str(labels[value])) for value in ordered]


def is_format_allowed(
    package_format: str, vendor: str = "", brand_slug: str = ""
) -> bool:
    """True when ``package_format`` may be used for a device of this vendor."""
    return package_format in allowed_formats(vendor, brand_slug)


def format_error(package_format: str, vendor: str = "", brand_slug: str = "") -> str:
    """A user-facing message when the format does not fit the vendor, else ``""``."""
    if not package_format or is_format_allowed(package_format, vendor, brand_slug):
        return ""
    labels = dict(FirmwarePackageFormat.choices)
    label = labels.get(package_format, package_format)
    return (
        f"“{label}” is not a package format for "
        f"{_effective_vendor(vendor, brand_slug)} devices. "
        "Choose one of: "
        + ", ".join(str(labels[value]) for value in allowed_formats(vendor, brand_slug))
        + "."
    )


def device_format_error(
    package_format: str,
    *,
    variant: Any = None,
    model: Any = None,
    brand: Any = None,
    chipset_text: str = "",
) -> str:
    """Error message when ``package_format`` does not fit the device, else ``""``.

    The vendor comes from the variant's or model's chipset (or free chipset text);
    the brand decides Samsung (Odin) and Apple devices.
    """
    brand = brand or getattr(model, "brand", None)
    return format_error(
        package_format,
        vendor_for_device(variant=variant, model=model, chipset_text=chipset_text),
        getattr(brand, "slug", "") or "",
    )


def tool_for_format(package_format: str) -> FlashTool | None:
    """The flashing tool matching a package format, if it has a dedicated one."""
    return FORMAT_TOOLS.get(package_format)


def vendor_summary(vendor: str) -> str:
    """One sentence describing the vendor's native package (empty when none)."""
    profile = VENDOR_PROFILES.get(canonical_vendor(vendor))
    return profile.summary if profile else ""
