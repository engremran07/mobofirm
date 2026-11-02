"""
Chipset-aware tool matching service.

Central intelligence layer for recommending correct flashing tools
based on device brand, chipset, and guide type. Prevents wrong
recommendations like Odin for Alcatel or SP Flash Tool for Apple.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import FlashingTool

logger = logging.getLogger(__name__)

# ── Known device chipsets ────────────────────────────────────────────
# Maps (brand_name_lower, model_name_lower) → real chipset string.
# Used to populate empty Model.chipset fields.

KNOWN_CHIPSETS: dict[tuple[str, str], str] = {
    # Apple
    ("apple", "iphone 15 pro max"): "Apple A17 Pro",
    # Xiaomi
    ("xiaomi", "technology"): "Qualcomm Snapdragon",
    # Asus
    ("asus", "rog phone 9 fe"): "Qualcomm Snapdragon 8 Gen 3",
    # Alcatel
    ("alcatel", "1"): "MediaTek MT6739",
    ("alcatel", "5"): "MediaTek MT6750",
    ("alcatel", "5v"): "MediaTek MT6739",
    ("alcatel", "7"): "MediaTek MT6763 (Helio P23)",
    ("alcatel", "tetra"): "Qualcomm Snapdragon 210",
    # Acer phones
    ("acer", "liquid z6"): "MediaTek MT6737",
    ("acer", "liquid z6 plus"): "MediaTek MT6750",
    ("acer", "acerone liquid s162e4"): "MediaTek MT6739",
    ("acer", "acerone liquid s262f5"): "MediaTek MT6739",
    ("acer", "acerone liquid s272e4"): "MediaTek MT6739",
    # Acer tablets
    ("acer", "iconia a14"): "MediaTek MT8167",
    ("acer", "iconia a16"): "MediaTek MT8167",
    ("acer", "iconia v11"): "MediaTek MT8167",
    ("acer", "iconia v12"): "MediaTek MT8163",
    ("acer", "iconia x12"): "MediaTek MT8176",
    ("acer", "iconia x14"): "MediaTek MT8176",
    ("acer", "iconia talk s"): "MediaTek MT8735P",
    ("acer", "iconia tab 10 a3-a40"): "MediaTek MT8163",
    ("acer", "chromebook tab 10"): "Rockchip OP1 (RK3399)",
    ("acer", "super zx"): "Qualcomm Snapdragon 680",
    ("acer", "super zx pro"): "Qualcomm Snapdragon 778G",
}

# ── Brand-default chipset families ──────────────────────────────────
# Fallback when model-specific chipset is unknown.

BRAND_DEFAULT_CHIPSET: dict[str, str] = {
    "alcatel": "MediaTek",
    "acer": "MediaTek",
    "apple": "Apple",
    "asus": "Qualcomm",
    "google": "Google Tensor",
    "honor": "Qualcomm",
    "huawei": "Kirin",
    "infinix": "MediaTek",
    "itel": "Unisoc",
    "lenovo": "Qualcomm",
    "lg": "Qualcomm",
    "motorola": "Qualcomm",
    "nokia": "Qualcomm",
    "nothing": "Qualcomm",
    "oneplus": "Qualcomm",
    "oppo": "Qualcomm",
    "poco": "Qualcomm",
    "realme": "MediaTek",
    "redmi": "Qualcomm",
    "samsung": "Qualcomm",
    "sony": "Qualcomm",
    "tecno": "MediaTek",
    "vivo": "Qualcomm",
    "xiaomi": "Qualcomm",
    "zte": "Qualcomm",
}

# ── Brand-restricted tools ──────────────────────────────────────────
# Tools that only work with specific brands, regardless of chipset.

BRAND_RESTRICTED_TOOLS: dict[str, list[str]] = {
    "odin": ["Samsung"],
    "samsung frija": ["Samsung"],
    "samsung frp helper": ["Samsung"],
    "miflash tool": ["Xiaomi", "Redmi", "Poco"],
    "mi unlock tool": ["Xiaomi", "Redmi", "Poco"],
    "xiaomitool v2": ["Xiaomi", "Redmi", "Poco"],
}

# ── Non-Android platforms (no standard flashing tools apply) ────────

NON_ANDROID_BRANDS = {"Apple"}


def get_chipset_family(chipset: str) -> str:
    """Extract chipset family name from a full chipset string.

    >>> get_chipset_family("MediaTek MT6739")
    'MediaTek'
    >>> get_chipset_family("Qualcomm Snapdragon 8 Gen 3")
    'Qualcomm'
    >>> get_chipset_family("Apple A17 Pro")
    'Apple'
    """
    if not chipset:
        return ""
    low = chipset.lower()
    if any(k in low for k in ("mediatek", "mt6", "mt8", "helio", "dimensity")):
        return "MediaTek"
    if any(k in low for k in ("qualcomm", "snapdragon", "msm8", "sdm", "sm8")):
        return "Qualcomm"
    if "exynos" in low:
        return "Samsung Exynos"
    if any(k in low for k in ("unisoc", "spreadtrum", "sc9")):
        return "Unisoc"
    if "apple" in low:
        return "Apple"
    if any(k in low for k in ("rockchip", "rk3")):
        return "Rockchip"
    if "tensor" in low:
        return "Google Tensor"
    if "kirin" in low:
        return "Kirin"
    return ""


def resolve_chipset(brand_name: str, model_name: str) -> str:
    """Resolve chipset for a brand+model using known data or brand defaults.

    Returns full chipset string (e.g. "MediaTek MT6739") if known,
    or family name (e.g. "MediaTek") as fallback.
    """
    key = (brand_name.lower().strip(), model_name.lower().strip())
    known = KNOWN_CHIPSETS.get(key)
    if known:
        return known
    # Brand default
    return BRAND_DEFAULT_CHIPSET.get(brand_name.lower().strip(), "")


def is_tool_compatible(tool_name: str, brand_name: str, chipset_family: str) -> bool:
    """Check if a specific tool is compatible with a brand+chipset.

    Checks both chipset and brand restrictions.
    """
    tool_lower = tool_name.lower().strip()

    # Check brand restriction
    restricted_brands = BRAND_RESTRICTED_TOOLS.get(tool_lower)
    if restricted_brands and brand_name not in restricted_brands:
        return False

    # Apple devices don't use standard Android flashing tools
    if brand_name in NON_ANDROID_BRANDS:
        return False

    return True


def get_compatible_tools_from_db(
    brand_name: str,
    chipset: str,
    *,
    guide_type: str = "",
    limit: int = 8,
) -> list[FlashingTool]:
    """Query DB for compatible flashing tools based on brand and chipset.

    Filters by chipset family and removes brand-incompatible tools.
    Returns tools ordered by relevance (featured first).
    """
    from .models import FlashingTool

    family = get_chipset_family(chipset)

    if brand_name in NON_ANDROID_BRANDS:
        return []

    qs = FlashingTool.objects.filter(is_active=True)

    # Filter by chipset family if known
    if family:
        qs = qs.filter(supported_chipsets__contains=family)

    tools = list(qs.order_by("-is_featured", "name")[:20])

    # Remove brand-restricted tools that don't match
    compatible: list[FlashingTool] = []
    for tool in tools:
        if is_tool_compatible(tool.name, brand_name, family):
            compatible.append(tool)

    return compatible[:limit]


def filter_tools_for_brand(
    tools: list[FlashingTool],
    brand_name: str,
    chipset: str = "",
) -> list[FlashingTool]:
    """Filter an existing tool list, removing incompatible ones.

    Used to validate template-assigned tools against a specific device.
    """
    family = get_chipset_family(chipset)

    if brand_name in NON_ANDROID_BRANDS:
        return []

    result: list[FlashingTool] = []
    for tool in tools:
        if not is_tool_compatible(tool.name, brand_name, family):
            continue
        # If we know the chipset family, also check chipset compatibility
        if family and hasattr(tool, "supported_chipsets"):
            tool_chipsets: list[str] = tool.supported_chipsets or []
            if tool_chipsets and family not in tool_chipsets:
                continue
        result.append(tool)
    return result


def get_primary_flash_tool_name(brand_name: str, chipset: str) -> str:
    """Get the name of the PRIMARY flash tool for a brand+chipset.

    Used for inline text references in blog posts.
    """
    family = get_chipset_family(chipset)
    brand_lower = brand_name.lower()

    if brand_name in NON_ANDROID_BRANDS:
        return "iTunes/Finder"

    # Brand-specific primary tools
    if brand_lower == "samsung":
        return "Odin"
    if brand_lower in ("xiaomi", "redmi", "poco"):
        return "MiFlash Tool"

    # Chipset-based primary tools
    chipset_tools: dict[str, str] = {
        "MediaTek": "SP Flash Tool",
        "Qualcomm": "QFIL",
        "Unisoc": "SPD Flash Tool",
        "Samsung Exynos": "Odin",
        "Rockchip": "RKDevTool",
    }
    return chipset_tools.get(family, "ADB & Fastboot")


def get_tool_list_text(brand_name: str, chipset: str) -> str:
    """Get a human-readable tool list for inline text in blog posts.

    Returns something like "SP Flash Tool or MCT MediaTek Client Tool"
    instead of the wrong "SP Flash Tool, Odin, or QFIL".
    """
    family = get_chipset_family(chipset)
    brand_lower = brand_name.lower()

    if brand_name in NON_ANDROID_BRANDS:
        return "iTunes or Finder (standard Apple restore tools)"

    # Build chipset-appropriate list
    if brand_lower == "samsung":
        return "Odin or Samsung Frija"
    if brand_lower in ("xiaomi", "redmi", "poco"):
        return "MiFlash Tool or XiaomiTool V2"

    family_tools: dict[str, str] = {
        "MediaTek": "SP Flash Tool or MCT MediaTek Client Tool",
        "Qualcomm": "QFIL (Qualcomm Flash Image Loader) or QPST",
        "Unisoc": "SPD Flash Tool or SPD Upgrade Tool",
        "Samsung Exynos": "Odin",
        "Rockchip": "RKDevTool or ADB & Fastboot",
        "Google Tensor": "ADB & Fastboot",
        "Kirin": "HiSuite or ADB & Fastboot",
    }
    return family_tools.get(family, "ADB & Fastboot")


def get_guide_tools_for_template(
    guide_type: str,
    brand_name: str,
    chipset: str,
) -> list[str]:
    """Get ordered tool names for a guide template by type.

    Returns the RIGHT tools for the guide's purpose + device.
    """
    family = get_chipset_family(chipset)
    brand_lower = brand_name.lower()
    primary = get_primary_flash_tool_name(brand_name, chipset)

    if brand_name in NON_ANDROID_BRANDS:
        return ["iTunes/Finder"]

    # Base tools always needed
    base: list[str] = ["ADB & Fastboot"]

    if guide_type in ("stock_flash", "downgrade", "repair"):
        tools = [primary]
        if family == "MediaTek":
            tools.append("MCT MediaTek Client Tool")
        tools.extend(base)
        return tools

    if guide_type == "custom_rom":
        return ["TWRP (Team Win Recovery Project)", "Magisk"] + base

    if guide_type == "unlock_bootloader":
        if brand_lower in ("xiaomi", "redmi", "poco"):
            return ["Mi Unlock Tool"] + base
        return base

    if guide_type == "root":
        return ["Magisk"] + base

    if guide_type == "recovery":
        return ["TWRP (Team Win Recovery Project)"] + base

    if guide_type == "frp_bypass":
        tools: list[str] = []
        if brand_lower == "samsung":
            tools.append("Samsung FRP Helper")
        tools.extend(["UMT (Ultimate Multi Tool)", "UnlockTool"])
        return tools

    # General fallback
    return [primary] + base
