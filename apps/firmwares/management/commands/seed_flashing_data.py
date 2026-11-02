"""
Management command to seed flashing tools, categories, guide templates,
and generated flashing guides for visual smoke testing.

Creates:
- 8 FlashingToolCategory entries
- 20 FlashingTool entries (real-world tools with realistic metadata)
- 9 FlashingGuideTemplate entries (one per guide type)
- 12 GeneratedFlashingGuide entries linked to blog posts

Usage:
    python manage.py seed_flashing_data [--clear] [--settings=app.settings_dev]
"""

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.blog.models import Category as BlogCategory
from apps.blog.models import Post, PostStatus
from apps.firmwares.models import (
    Brand,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    Model,
)

# ── Category seed data ──────────────────────────────────────────────

CATEGORIES = [
    {
        "name": "MediaTek Tools",
        "slug": "mediatek-tools",
        "description": "Tools for flashing devices with MediaTek (MTK) chipsets.",
        "icon": "cpu",
        "sort_order": 1,
    },
    {
        "name": "Qualcomm Tools",
        "slug": "qualcomm-tools",
        "description": "EDL/Sahara protocol tools for Qualcomm-based devices.",
        "icon": "zap",
        "sort_order": 2,
    },
    {
        "name": "Samsung Tools",
        "slug": "samsung-tools",
        "description": "Dedicated tools for Samsung Galaxy firmware flashing.",
        "icon": "smartphone",
        "sort_order": 3,
    },
    {
        "name": "Universal Flash Tools",
        "slug": "universal-flash-tools",
        "description": "Multi-brand, multi-chipset flashing and servicing tools.",
        "icon": "wrench",
        "sort_order": 4,
    },
    {
        "name": "Unlock & FRP Tools",
        "slug": "unlock-frp-tools",
        "description": "Bootloader unlock, FRP bypass, and SIM unlock utilities.",
        "icon": "unlock",
        "sort_order": 5,
    },
    {
        "name": "Root & Recovery",
        "slug": "root-recovery",
        "description": "Rooting frameworks, custom recovery, and partition tools.",
        "icon": "shield",
        "sort_order": 6,
    },
    {
        "name": "Spreadtrum / Unisoc Tools",
        "slug": "spreadtrum-unisoc-tools",
        "description": "Flash tools for Spreadtrum/Unisoc SC-series chipsets.",
        "icon": "hard-drive",
        "sort_order": 7,
    },
    {
        "name": "Xiaomi / MIUI Tools",
        "slug": "xiaomi-miui-tools",
        "description": "Official and community tools for Xiaomi, Redmi and POCO devices.",
        "icon": "box",
        "sort_order": 8,
    },
]

# ── Tool seed data ──────────────────────────────────────────────────

TOOLS = [
    # MediaTek
    {
        "name": "SP Flash Tool",
        "category_slug": "mediatek-tools",
        "description": "Official MediaTek Smart-Phone Flash Tool for downloading firmware to MTK-based devices via scatter file.",
        "tool_type": "free",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["MediaTek"],
        "version": "5.2352",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://spflashtool.com/",
        "download_url": "https://spflashtool.com/download/",
        "instructions": "## SP Flash Tool Usage\n\n1. Install MTK USB VCOM drivers\n2. Open SP Flash Tool and load scatter file\n3. Select Download Agent (DA)\n4. Choose download method (Download Only / Firmware Upgrade)\n5. Click **Download** and connect device in BROM mode\n6. Wait for the green circle ✓",
        "features": [
            "Scatter file loading",
            "Download-only / Firmware-upgrade / Write-memory",
            "Format + Download",
            "Memory test",
            "BROM / Preloader modes",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "MTK USB VCOM drivers",
            "Scatter file from firmware package",
        ],
    },
    {
        "name": "MCT MediaTek Client Tool",
        "category_slug": "mediatek-tools",
        "description": "Advanced MediaTek service tool with DA agent patching, BROM exploit, and auth bypass for secured devices.",
        "tool_type": "crack",
        "platform": "windows",
        "risk_level": "advanced",
        "chipsets": ["MediaTek"],
        "version": "5.4",
        "is_free": False,
        "is_featured": False,
        "official_url": "",
        "download_url": "",
        "instructions": "## MCT Tool\n\n1. Launch MCT and select chipset family\n2. Load custom DA or use built-in exploit\n3. Choose operation: Read / Write / Format / FRP\n4. Connect device in BROM mode\n5. Execute operation",
        "features": [
            "DA bypass for locked devices",
            "BROM exploit",
            "FRP remove",
            "Pattern/PIN remove",
            "Read/write individual partitions",
        ],
        "requirements": [
            "Windows 10/11",
            "MTK USB VCOM drivers",
            "USB 2.0 port recommended",
        ],
    },
    # Qualcomm
    {
        "name": "QFIL (Qualcomm Flash Image Loader)",
        "category_slug": "qualcomm-tools",
        "description": "Official Qualcomm tool for flashing via EDL (9008) mode. Part of the QPST package.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["Qualcomm"],
        "version": "2.0.3.5",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://qpsttool.com/",
        "download_url": "https://qpsttool.com/qfil-tool",
        "instructions": "## QFIL Usage\n\n1. Install Qualcomm HS-USB QDLoader 9008 drivers\n2. Boot device into EDL mode (9008)\n3. Open QFIL, select flat build / raw program XML\n4. Browse to rawprogram0.xml and patch0.xml\n5. Click **Download** and wait for completion",
        "features": [
            "Flat build flashing",
            "Raw programming (Firehose)",
            "Sahara protocol",
            "Multi-image download",
            "Partition backup",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "Qualcomm USB drivers",
            "Firehose programmer (.elf/.mbn)",
        ],
    },
    {
        "name": "QPST (Qualcomm Product Support Tools)",
        "category_slug": "qualcomm-tools",
        "description": "Comprehensive Qualcomm diagnostic suite: EFS Explorer, QXDM, Software Download, NV backup/restore.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "advanced",
        "chipsets": ["Qualcomm"],
        "version": "2.7.496",
        "is_free": True,
        "is_featured": False,
        "official_url": "https://qpsttool.com/",
        "download_url": "https://qpsttool.com/qpst-tool",
        "instructions": "## QPST\n\n1. Install QPST suite\n2. Open QPST Configuration to detect COM port\n3. Use Software Download for flashing or EFS Explorer for NV items\n4. Always backup EFS before operations",
        "features": [
            "EFS Explorer",
            "NV item backup/restore",
            "Software Download",
            "COM port manager",
            "DIAG mode interface",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "Qualcomm USB drivers",
            "Device in DIAG mode",
        ],
    },
    # Samsung
    {
        "name": "Odin",
        "category_slug": "samsung-tools",
        "description": "Legendary Samsung firmware flash tool. Flashes .tar / .tar.md5 files via Download mode (Odin mode).",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "safe",
        "chipsets": ["Qualcomm", "Samsung Exynos"],
        "version": "3.14.4",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://odindownload.com/",
        "download_url": "https://odindownload.com/download/",
        "instructions": "## Odin Flash Guide\n\n1. Download correct firmware (AP/BL/CP/CSC)\n2. Boot Samsung device into **Download Mode** (Vol Down + Power)\n3. Open Odin and load files into AP/BL/CP/CSC slots\n4. Ensure **Auto Reboot** is checked\n5. Click **Start** — wait for PASS! message",
        "features": [
            "AP/BL/CP/CSC slot loading",
            "Auto reboot",
            "PIT repartition",
            "Reset time counter",
            "NAND erase",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "Samsung USB drivers",
            "Device in Download Mode",
        ],
    },
    {
        "name": "Samsung Frija",
        "category_slug": "samsung-tools",
        "description": "Free Samsung firmware downloader that fetches official firmware directly from Samsung FUS servers.",
        "tool_type": "free",
        "platform": "windows",
        "risk_level": "safe",
        "chipsets": ["Qualcomm", "Samsung Exynos"],
        "version": "1.4.4",
        "is_free": True,
        "is_featured": False,
        "official_url": "",
        "download_url": "",
        "instructions": "## Frija\n\n1. Enter device model number (e.g. SM-G991B) and CSC region\n2. Click **Check Update**\n3. Download the latest firmware\n4. Flash with Odin",
        "features": [
            "Direct FUS server access",
            "Auto-decrypt firmware",
            "Region/CSC selection",
            "Latest firmware check",
        ],
        "requirements": ["Windows 10/11", ".NET Framework 4.7.2+"],
    },
    # Universal
    {
        "name": "UMT (Ultimate Multi Tool)",
        "category_slug": "universal-flash-tools",
        "description": "Professional all-in-one servicing solution: flash, unlock, repair IMEI, FRP remove for 70+ brands.",
        "tool_type": "crack",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["Qualcomm", "MediaTek", "Samsung Exynos", "Unisoc"],
        "version": "9.8",
        "is_free": False,
        "is_featured": True,
        "official_url": "https://ultimatemultitool.com/",
        "download_url": "",
        "instructions": "## UMT Dongle\n\n1. Plug in UMT dongle\n2. Select brand tab (Samsung / Xiaomi / Oppo / etc.)\n3. Detect device via ADB or special mode\n4. Choose operation: Flash / Unlock / FRP / IMEI\n5. Execute and wait for result",
        "features": [
            "70+ brand support",
            "FRP remove",
            "IMEI repair",
            "Pattern/PIN unlock",
            "Firmware read/write",
            "Network unlock",
        ],
        "requirements": [
            "Windows 10/11",
            "UMT dongle or QcFire",
            "Brand-specific USB drivers",
        ],
    },
    {
        "name": "Miracle Box / Thunder",
        "category_slug": "universal-flash-tools",
        "description": "Full-featured GSM servicing box supporting MTK, Qualcomm, Spreadtrum and more. Hardware-based authentication.",
        "tool_type": "crack",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["Qualcomm", "MediaTek", "Unisoc"],
        "version": "3.58",
        "is_free": False,
        "is_featured": True,
        "official_url": "https://miracleteam.org/",
        "download_url": "",
        "instructions": "## Miracle Box\n\n1. Connect Miracle Box dongle\n2. Select platform tab (MTK / Qualcomm / SPD)\n3. Connect device and detect\n4. Select operation: Flash / Unlock / Format / FRP\n5. Execute",
        "features": [
            "MTK/Qualcomm/SPD support",
            "IMEI write",
            "FRP remove",
            "Network unlock",
            "Read firmware",
            "Format userdata",
        ],
        "requirements": [
            "Windows 10/11",
            "Miracle Box dongle",
            "Platform-specific drivers",
        ],
    },
    {
        "name": "UnlockTool",
        "category_slug": "universal-flash-tools",
        "description": "Cloud-based professional GSM tool with Samsung, Xiaomi, Oppo, Vivo, Motorola and Qualcomm generic support.",
        "tool_type": "crack",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["Qualcomm", "MediaTek", "Samsung Exynos"],
        "version": "2024.08.01",
        "is_free": False,
        "is_featured": False,
        "official_url": "https://unlocktool.net/",
        "download_url": "",
        "instructions": "## UnlockTool\n\n1. Login with purchased credits\n2. Select brand tab\n3. Connect device in appropriate mode\n4. Choose operation\n5. Execute — credits deducted per operation",
        "features": [
            "Cloud-based authentication",
            "Samsung FRP/unlock",
            "Xiaomi account remove",
            "Oppo/Vivo unlock",
            "Motorola FRP",
        ],
        "requirements": ["Windows 10/11", "Internet connection", "UnlockTool credits"],
    },
    # Unlock & FRP
    {
        "name": "ADB & Fastboot",
        "category_slug": "unlock-frp-tools",
        "description": "Google's official Android Debug Bridge and Fastboot — essential sideloading, flashing, and debugging utilities.",
        "tool_type": "open_source",
        "platform": "multi",
        "risk_level": "safe",
        "chipsets": [
            "Qualcomm",
            "MediaTek",
            "Samsung Exynos",
            "Unisoc",
            "Google Tensor",
        ],
        "version": "35.0.1",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://developer.android.com/tools/releases/platform-tools",
        "download_url": "https://developer.android.com/tools/releases/platform-tools",
        "instructions": "## ADB & Fastboot\n\n1. Download Platform Tools from Google\n2. Enable USB Debugging on device\n3. Run `adb devices` to verify connection\n4. Use `adb sideload <file>` or `fastboot flash <partition> <image>`",
        "features": [
            "ADB sideload",
            "Fastboot flash",
            "OEM unlock",
            "Partition management",
            "Logcat debugging",
            "Shell access",
        ],
        "requirements": [
            "Windows / macOS / Linux",
            "USB Debugging enabled",
            "OEM USB drivers",
        ],
    },
    {
        "name": "Samsung FRP Helper",
        "category_slug": "unlock-frp-tools",
        "description": "Specialized tool for bypassing Factory Reset Protection on Samsung devices via ADB/MTP exploits.",
        "tool_type": "free",
        "platform": "windows",
        "risk_level": "advanced",
        "chipsets": ["Qualcomm", "Samsung Exynos"],
        "version": "0.2",
        "is_free": True,
        "is_featured": False,
        "official_url": "",
        "download_url": "",
        "instructions": "## Samsung FRP Helper\n\n1. Connect Samsung in MTP mode\n2. Select Android version\n3. Choose bypass method\n4. Follow on-screen instructions",
        "features": [
            "ADB-based FRP bypass",
            "MTP exploit",
            "Multiple Android version support",
            "One-click operation",
        ],
        "requirements": [
            "Windows 10/11",
            "Samsung USB drivers",
            "Device with FRP lock",
        ],
    },
    # Root & Recovery
    {
        "name": "Magisk",
        "category_slug": "root-recovery",
        "description": "The premier systemless root framework for Android. Modules, MagiskHide, Zygisk, and SafetyNet bypass.",
        "tool_type": "open_source",
        "platform": "android",
        "risk_level": "advanced",
        "chipsets": [
            "Qualcomm",
            "MediaTek",
            "Samsung Exynos",
            "Unisoc",
            "Google Tensor",
        ],
        "version": "27.0",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://github.com/topjohnwu/Magisk",
        "download_url": "https://github.com/topjohnwu/Magisk/releases",
        "instructions": "## Magisk Root\n\n1. Extract boot.img from firmware\n2. Patch boot.img with Magisk app\n3. Flash patched boot via Fastboot:\n   `fastboot flash boot magisk_patched.img`\n4. Reboot and open Magisk app to verify root",
        "features": [
            "Systemless root",
            "Magisk Modules",
            "MagiskHide / Zygisk",
            "SafetyNet bypass",
            "Boot image patching",
            "OTA survival",
        ],
        "requirements": [
            "Unlocked bootloader",
            "Stock boot.img",
            "Magisk APK installed",
        ],
    },
    {
        "name": "TWRP (Team Win Recovery Project)",
        "category_slug": "root-recovery",
        "description": "Custom recovery with touch interface. Flash ZIPs, create backups, wipe partitions, ADB sideload.",
        "tool_type": "open_source",
        "platform": "android",
        "risk_level": "moderate",
        "chipsets": ["Qualcomm", "MediaTek", "Samsung Exynos", "Unisoc"],
        "version": "3.7.1",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://twrp.me/",
        "download_url": "https://twrp.me/Devices/",
        "instructions": "## TWRP Install\n\n1. Unlock bootloader\n2. Download device-specific TWRP image\n3. Flash via Fastboot:\n   `fastboot flash recovery twrp.img`\n4. Boot into recovery (Vol Up + Power)\n5. Create full Nandroid backup first!",
        "features": [
            "Touch-based UI",
            "Nandroid backup/restore",
            "Flash ZIP/IMG",
            "Partition management",
            "ADB sideload",
            "Decryption support",
        ],
        "requirements": [
            "Unlocked bootloader",
            "Device-specific TWRP build",
            "Fastboot or Odin",
        ],
    },
    {
        "name": "KingoRoot",
        "category_slug": "root-recovery",
        "description": "One-click root solution for older Android devices. Supports Android 2.x to 8.x via APK or desktop.",
        "tool_type": "free",
        "platform": "multi",
        "risk_level": "risky",
        "chipsets": ["Qualcomm", "MediaTek"],
        "version": "4.9.6",
        "is_free": True,
        "is_featured": False,
        "official_url": "https://www.kingoapp.com/",
        "download_url": "https://www.kingoapp.com/",
        "instructions": "## KingoRoot\n\n1. Enable USB Debugging\n2. Install KingoRoot on PC or APK on device\n3. Click **Root** button\n4. Wait for process to complete\n\n⚠️ Not recommended for Android 9+ devices",
        "features": [
            "One-click root",
            "APK + Desktop versions",
            "Multi-device support",
            "Unroot option",
        ],
        "requirements": [
            "Windows 7+ or Android APK",
            "USB Debugging",
            "Android 2.x - 8.x recommended",
        ],
    },
    # Spreadtrum / Unisoc
    {
        "name": "SPD Flash Tool (Research Download)",
        "category_slug": "spreadtrum-unisoc-tools",
        "description": "Official Spreadtrum/Unisoc Research Download tool for flashing .pac firmware packages.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "moderate",
        "chipsets": ["Unisoc"],
        "version": "R25.20.5001",
        "is_free": True,
        "is_featured": False,
        "official_url": "",
        "download_url": "",
        "instructions": "## SPD Research Download\n\n1. Install Spreadtrum USB drivers\n2. Open Research Download tool\n3. Load .pac firmware file\n4. Click **Start Downloading**\n5. Connect powered-off device via USB\n6. Wait for green PASS indicator",
        "features": [
            ".pac firmware flashing",
            "BROM mode",
            "Partition read/write",
            "NV backup",
            "Reset to factory",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "Spreadtrum USB drivers",
            ".pac firmware file",
        ],
    },
    {
        "name": "SPD Upgrade Tool",
        "category_slug": "spreadtrum-unisoc-tools",
        "description": "Simplified Unisoc firmware upgrade utility for .pac files. Lighter alternative to Research Download.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "safe",
        "chipsets": ["Unisoc"],
        "version": "2.2",
        "is_free": True,
        "is_featured": False,
        "official_url": "",
        "download_url": "",
        "instructions": "## SPD Upgrade Tool\n\n1. Install Spreadtrum USB drivers\n2. Load .pac file\n3. Click **Start**\n4. Connect device in download mode",
        "features": [
            ".pac firmware upgrade",
            "Simple one-click interface",
            "Progress indicator",
        ],
        "requirements": [
            "Windows 7/8/10/11",
            "Spreadtrum USB drivers",
            ".pac firmware file",
        ],
    },
    # Xiaomi
    {
        "name": "MiFlash Tool",
        "category_slug": "xiaomi-miui-tools",
        "description": "Official Xiaomi flash tool for flashing Fastboot ROMs (.tgz) on Xiaomi, Redmi, and POCO devices.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "safe",
        "chipsets": ["Qualcomm", "MediaTek"],
        "version": "2024.01.26",
        "is_free": True,
        "is_featured": True,
        "official_url": "https://xiaomiflashtool.com/",
        "download_url": "https://xiaomiflashtool.com/",
        "instructions": "## MiFlash\n\n1. Unlock bootloader via mi.com/unlock\n2. Extract Fastboot ROM\n3. Boot device into Fastboot mode\n4. Open MiFlash, browse to extracted folder\n5. Select flash option (clean all / save user data)\n6. Click **Flash**",
        "features": [
            "Fastboot ROM flashing",
            "Clean all / Save user data",
            "EDL mode support",
            "Anti-rollback check",
            "Script-based flashing",
        ],
        "requirements": [
            "Windows 10/11",
            "Xiaomi USB drivers",
            "Unlocked bootloader",
            "Fastboot ROM .tgz",
        ],
    },
    {
        "name": "Mi Unlock Tool",
        "category_slug": "xiaomi-miui-tools",
        "description": "Official Xiaomi bootloader unlock tool. Requires Mi account approval and waiting period.",
        "tool_type": "oem",
        "platform": "windows",
        "risk_level": "safe",
        "chipsets": ["Qualcomm", "MediaTek"],
        "version": "7.6.727.43",
        "is_free": True,
        "is_featured": False,
        "official_url": "https://en.miui.com/unlock/",
        "download_url": "https://en.miui.com/unlock/download_en.html",
        "instructions": "## Mi Unlock\n\n1. Apply for unlock permission at en.miui.com/unlock\n2. Wait for approval (72h-720h)\n3. Boot device into Fastboot mode\n4. Open Mi Unlock and sign in\n5. Click **Unlock** — device will be wiped",
        "features": [
            "Official bootloader unlock",
            "Mi Account verification",
            "Waiting period enforcement",
            "One-click unlock",
        ],
        "requirements": [
            "Windows 10/11",
            "Mi Account with unlock approval",
            "Device bound to Mi Account",
        ],
    },
    {
        "name": "XiaomiTool V2",
        "category_slug": "xiaomi-miui-tools",
        "description": "Community Xiaomi ROM downloader and flasher. Download and flash official ROMs without manual steps.",
        "tool_type": "free",
        "platform": "multi",
        "risk_level": "safe",
        "chipsets": ["Qualcomm", "MediaTek"],
        "version": "20.7.28",
        "is_free": True,
        "is_featured": False,
        "official_url": "https://www.xiaomitool.com/V2/",
        "download_url": "https://www.xiaomitool.com/V2/",
        "instructions": "## XiaomiTool V2\n\n1. Install and launch XiaomiTool\n2. Connect Xiaomi device via USB\n3. Auto-detects device model and available ROMs\n4. Select ROM version to install\n5. Automated download + flash process",
        "features": [
            "Auto device detection",
            "ROM browser",
            "Automated flashing",
            "Cross-platform (Java)",
            "Fastboot + Recovery support",
        ],
        "requirements": [
            "Windows / macOS / Linux",
            "Java runtime",
            "Xiaomi USB drivers",
        ],
    },
]

# ── Brand ↔ Tool mapping for M2M ────────────────────────────────────

BRAND_TOOL_MAP: dict[str, list[str]] = {
    "Odin": ["Samsung"],
    "Samsung Frija": ["Samsung"],
    "Samsung FRP Helper": ["Samsung"],
    "MiFlash Tool": ["Xiaomi", "Redmi", "Poco"],
    "Mi Unlock Tool": ["Xiaomi", "Redmi", "Poco"],
    "XiaomiTool V2": ["Xiaomi", "Redmi", "Poco"],
}

# ── Guide template seed data ────────────────────────────────────────

GUIDE_TEMPLATES = [
    {
        "guide_type": "stock_flash",
        "title_template": "How to Flash Stock Firmware on {brand} {model} — Step-by-Step Guide",
        "body_template": (
            "## Flash Stock Firmware on {brand} {model}\n\n"
            "This guide walks you through restoring the official stock firmware on your "
            "**{brand} {model}** ({chipset} chipset). Follow each step carefully.\n\n"
            "### Prerequisites\n\n"
            "- Charge your device to at least 50%\n"
            "- Back up all important data — this process will wipe your device\n"
            "- Download the correct firmware for your exact model and region\n\n"
            "### Required Tools\n\n{tools}\n\n"
            "### Steps\n\n{steps}\n\n"
            "### Troubleshooting\n\n"
            "- **Device not detected?** Reinstall USB drivers and try a USB 2.0 port\n"
            "- **Stuck at logo?** Try a full factory reset from recovery mode\n"
            "- **Error during flash?** Re-download firmware — the archive may be corrupted"
        ),
        "summary_template": "Complete step-by-step guide to flash official stock firmware on {brand} {model} with {chipset} chipset. Includes tool links, driver setup, and troubleshooting tips.",
    },
    {
        "guide_type": "custom_rom",
        "title_template": "Install Custom ROM on {brand} {model} — Complete Guide",
        "body_template": (
            "## Custom ROM Installation — {brand} {model}\n\n"
            "Unlock the full potential of your **{brand} {model}** by installing a custom ROM.\n\n"
            "### Before You Start\n\n"
            "- Unlock bootloader first (see our bootloader unlock guide)\n"
            "- Install TWRP or other custom recovery\n"
            "- Full backup via TWRP Nandroid backup\n\n"
            "### Tools Needed\n\n{tools}\n\n"
            "### Installation Steps\n\n{steps}\n\n"
            "### Post-Install\n\n"
            "- Flash GApps if not included\n- Set up Magisk for root (optional)\n- Restore your data"
        ),
        "summary_template": "How to install a custom ROM on {brand} {model}. Covers bootloader unlock, TWRP recovery, ROM flash, and GApps setup.",
    },
    {
        "guide_type": "unlock_bootloader",
        "title_template": "Unlock Bootloader on {brand} {model} — Official Method",
        "body_template": (
            "## Bootloader Unlock — {brand} {model}\n\n"
            "Unlocking the bootloader is the first step to custom ROMs, root, and recovery.\n\n"
            "### ⚠️ Warning\n\nUnlocking the bootloader **will erase all data** and may void warranty.\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Official method to unlock the bootloader on {brand} {model}. Includes warnings, prerequisites, and step-by-step instructions.",
    },
    {
        "guide_type": "root",
        "title_template": "Root {brand} {model} with Magisk — Safe Method",
        "body_template": (
            "## Root {brand} {model}\n\n"
            "Gain full root access on your **{brand} {model}** using Magisk systemless root.\n\n"
            "### Prerequisites\n\n"
            "- Unlocked bootloader\n- Stock boot.img extracted from firmware\n"
            "- Magisk app installed\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Safe rooting guide for {brand} {model} using Magisk. Covers boot.img patching, flashing, and verification.",
    },
    {
        "guide_type": "recovery",
        "title_template": "Install TWRP Recovery on {brand} {model}",
        "body_template": (
            "## TWRP Custom Recovery — {brand} {model}\n\n"
            "Install Team Win Recovery Project (TWRP) on your **{brand} {model}** for advanced "
            "partition management, backups, and ROM flashing.\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Install TWRP custom recovery on {brand} {model}. Flash via Fastboot, create backups, and prepare for ROM installation.",
    },
    {
        "guide_type": "frp_bypass",
        "title_template": "FRP Bypass for {brand} {model} — Google Account Lock Removal",
        "body_template": (
            "## FRP Bypass — {brand} {model}\n\n"
            "Remove Factory Reset Protection (Google account lock) from your **{brand} {model}**.\n\n"
            "### ⚠️ Disclaimer\n\nOnly use this on devices you legally own.\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Remove FRP lock (Google account bypass) on {brand} {model}. Multiple methods covered with step-by-step instructions.",
    },
    {
        "guide_type": "downgrade",
        "title_template": "Downgrade {brand} {model} Firmware — Roll Back to Previous Version",
        "body_template": (
            "## Firmware Downgrade — {brand} {model}\n\n"
            "Roll back your **{brand} {model}** to a previous firmware version if the latest update "
            "caused issues.\n\n"
            "### ⚠️ Anti-Rollback Warning\n\nSome devices have anti-rollback protection. "
            "Check your anti-rollback index before proceeding.\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Downgrade firmware on {brand} {model}. Includes anti-rollback check, tool setup, and safe rollback procedure.",
    },
    {
        "guide_type": "repair",
        "title_template": "Unbrick {brand} {model} — Brick Repair Guide",
        "body_template": (
            "## Brick Repair — {brand} {model}\n\n"
            "Recover your bricked **{brand} {model}** back to working condition.\n\n"
            "### Types of Brick\n\n"
            "- **Soft brick**: Stuck at logo, boot loop — usually fixable via recovery\n"
            "- **Hard brick**: No response at all — requires EDL/BROM mode or hardware tools\n\n"
            "### Tools\n\n{tools}\n\n### Steps\n\n{steps}"
        ),
        "summary_template": "Unbrick your {brand} {model}. Covers soft brick (boot loop) and hard brick recovery methods.",
    },
    {
        "guide_type": "general",
        "title_template": "{brand} {model} — Complete Flashing & Service Guide",
        "body_template": (
            "## {brand} {model} — Service Guide\n\n"
            "Comprehensive guide covering firmware flash, root, recovery, unlock, and repair "
            "for the **{brand} {model}**.\n\n"
            "### Specifications\n\n- Chipset: {chipset}\n\n"
            "### Available Operations\n\n{tools}\n\n### Detailed Steps\n\n{steps}"
        ),
        "summary_template": "All-in-one service guide for {brand} {model}: flash, root, unlock, recover. Chipset: {chipset}.",
    },
]


class Command(BaseCommand):
    help = "Seed flashing tools, categories, guide templates, and generated guides for smoke testing"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing flashing data before seeding",
        )

    def handle(self, *args, **options):
        clear = options["clear"]

        if clear:
            self.stdout.write(self.style.WARNING("Clearing existing flashing data..."))
            GeneratedFlashingGuide.objects.all().delete()
            FlashingGuideTemplate.objects.all().delete()
            FlashingTool.objects.all().delete()
            FlashingToolCategory.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("  ✓ Cleared"))

        cats = self._create_categories()
        tools = self._create_tools(cats)
        self._assign_brands(tools)
        templates = self._create_guide_templates(tools)
        guides = self._create_generated_guides(templates)

        # Summary
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("✓ Flashing seed complete!"))
        self.stdout.write(f"  Categories:  {FlashingToolCategory.objects.count()}")
        self.stdout.write(f"  Tools:       {FlashingTool.objects.count()}")
        self.stdout.write(f"  Templates:   {FlashingGuideTemplate.objects.count()}")
        self.stdout.write(f"  Guides:      {GeneratedFlashingGuide.objects.count()}")
        self.stdout.write(f"  Blog posts:  {guides}")

    # ── Categories ───────────────────────────────────────────────────

    def _create_categories(self) -> dict[str, FlashingToolCategory]:
        self.stdout.write("\nCreating flashing tool categories...")
        cats: dict[str, FlashingToolCategory] = {}
        for data in CATEGORIES:
            cat, created = FlashingToolCategory.objects.get_or_create(
                slug=data["slug"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "icon": data["icon"],
                    "sort_order": data["sort_order"],
                },
            )
            cats[data["slug"]] = cat
            tag = "+" if created else "="
            self.stdout.write(f"  {tag} {cat.name}")
        self.stdout.write(self.style.SUCCESS(f"  ✓ {len(cats)} categories ready"))
        return cats

    # ── Tools ────────────────────────────────────────────────────────

    def _create_tools(
        self, cats: dict[str, FlashingToolCategory]
    ) -> dict[str, FlashingTool]:
        self.stdout.write("\nCreating flashing tools...")
        tools: dict[str, FlashingTool] = {}
        for data in TOOLS:
            slug = slugify(data["name"])
            category = cats[data["category_slug"]]
            tool, created = FlashingTool.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": data["name"],
                    "category": category,
                    "description": data["description"],
                    "tool_type": data["tool_type"],
                    "platform": data["platform"],
                    "risk_level": data["risk_level"],
                    "supported_chipsets": data["chipsets"],
                    "version": data["version"],
                    "is_free": data["is_free"],
                    "is_featured": data["is_featured"],
                    "official_url": data["official_url"],
                    "download_url": data["download_url"],
                    "instructions": data["instructions"],
                    "features": data["features"],
                    "requirements": data["requirements"],
                    "last_version_date": self._random_recent_date(),
                    "views_count": random.randint(500, 80000),  # noqa: S311
                    "downloads_count": random.randint(100, 40000),  # noqa: S311
                },
            )
            tools[data["name"]] = tool
            tag = "+" if created else "="
            self.stdout.write(f"  {tag} {tool.name} ({category.name})")
        self.stdout.write(self.style.SUCCESS(f"  ✓ {len(tools)} tools ready"))
        return tools

    # ── Brand ↔ Tool M2M ────────────────────────────────────────────

    def _assign_brands(self, tools: dict[str, FlashingTool]) -> None:
        self.stdout.write("\nAssigning supported brands to tools...")
        assigned = 0
        for tool_name, brand_names in BRAND_TOOL_MAP.items():
            tool = tools.get(tool_name)
            if not tool:
                continue
            brands = Brand.objects.filter(name__in=brand_names)
            if brands.exists():
                tool.supported_brands.add(*brands)
                assigned += brands.count()
        self.stdout.write(
            self.style.SUCCESS(f"  ✓ {assigned} brand-tool links created")
        )

    # ── Guide Templates ─────────────────────────────────────────────

    def _create_guide_templates(
        self, tools: dict[str, FlashingTool]
    ) -> list[FlashingGuideTemplate]:
        self.stdout.write("\nCreating flashing guide templates...")
        templates: list[FlashingGuideTemplate] = []
        # Assign a brand to some templates for variety
        brand_names = ["Samsung", "Xiaomi", "Huawei"]
        brands = {b.name: b for b in Brand.objects.filter(name__in=brand_names)}

        for i, data in enumerate(GUIDE_TEMPLATES):
            guide_type = data["guide_type"]
            # Alternate: some templates are brand-specific, some generic
            brand = None
            if i < len(brand_names):
                brand = brands.get(brand_names[i])

            tmpl, created = FlashingGuideTemplate.objects.get_or_create(
                guide_type=guide_type,
                brand=brand,
                defaults={
                    "title_template": data["title_template"],
                    "body_template": data["body_template"],
                    "summary_template": data["summary_template"],
                    "auto_generate": i % 3 == 0,
                    "auto_publish": False,
                    "is_active": True,
                },
            )
            # Assign recommended tools to template (chipset-aware)
            if created:
                from apps.firmwares.tool_matcher import (
                    get_guide_tools_for_template,
                    resolve_chipset,
                )

                brand_name = brand.name if brand else ""
                chipset = resolve_chipset(brand_name, "") if brand_name else ""
                correct_names = get_guide_tools_for_template(
                    guide_type, brand_name, chipset
                )
                recommended = [tools[n] for n in correct_names if n in tools]
                if not recommended:
                    # Fallback: ADB & Fastboot is always valid
                    adb = tools.get("ADB & Fastboot")
                    if adb:
                        recommended = [adb]
                tmpl.recommended_tools.add(*recommended)
            templates.append(tmpl)
            tag = "+" if created else "="
            brand_label = brand.name if brand else "Generic"
            self.stdout.write(
                f"  {tag} {tmpl.get_guide_type_display()} — {brand_label}"  # type: ignore[attr-defined]
            )
        self.stdout.write(self.style.SUCCESS(f"  ✓ {len(templates)} templates ready"))
        return templates

    # ── Generated Guides (with blog posts) ───────────────────────────

    def _create_generated_guides(self, templates: list[FlashingGuideTemplate]) -> int:
        self.stdout.write("\nGenerating flashing guide blog posts...")

        # Ensure a "Flashing Guides" blog category exists
        blog_cat, _ = BlogCategory.objects.get_or_create(
            slug="flashing-guides",
            defaults={"name": "Flashing Guides"},
        )

        # Get an author (first superuser or first user)
        from django.contrib.auth import get_user_model

        UserModel = get_user_model()
        author = UserModel.objects.filter(is_superuser=True).first()
        if not author:
            author = UserModel.objects.first()
        if not author:
            self.stdout.write(
                self.style.WARNING("  ⚠ No users exist — skipping generated guides")
            )
            return 0

        # Pick 12 brand/model combos for guide generation
        models_qs = (
            Model.objects.select_related("brand")
            .filter(is_active=True)
            .order_by("?")[:12]
        )
        device_models = list(models_qs)
        if not device_models:
            self.stdout.write(
                self.style.WARNING(
                    "  ⚠ No device models found — run seed_firmware_data first. Skipping."
                )
            )
            return 0

        created_count = 0
        now = timezone.now()

        for i, device_model in enumerate(device_models):
            tmpl = templates[i % len(templates)]
            brand = device_model.brand
            brand_name = brand.name
            model_name = device_model.name

            # Resolve real chipset instead of hardcoding
            from apps.firmwares.tool_matcher import (
                get_guide_tools_for_template,
                resolve_chipset,
            )

            chipset = device_model.chipset or resolve_chipset(brand_name, model_name)

            # Build chipset-aware tool list
            tool_names = get_guide_tools_for_template(
                tmpl.guide_type, brand_name, chipset
            )
            tools_text = "\n".join(f"- {name}" for name in tool_names)

            title = (
                tmpl.title_template.replace("{brand}", brand_name)
                .replace("{model}", model_name)
                .replace("{chipset}", chipset)
            )
            body = (
                tmpl.body_template.replace("{brand}", brand_name)
                .replace("{model}", model_name)
                .replace("{chipset}", chipset)
                .replace("{tools}", tools_text)
                .replace(
                    "{steps}",
                    (
                        "1. Download the correct firmware for your device\n"
                        "2. Install USB drivers for your device\n"
                        "3. Boot into the required mode (Fastboot / Download / EDL)\n"
                        "4. Load firmware in the flash tool\n"
                        "5. Click Start/Download and wait for completion\n"
                        "6. Reboot device and verify"
                    ),
                )
            )
            summary = (
                tmpl.summary_template.replace("{brand}", brand_name)
                .replace("{model}", model_name)
                .replace("{chipset}", chipset)
            )
            slug = slugify(f"{brand_name}-{model_name}-{tmpl.guide_type}")[:240]

            # Skip if already exists
            if Post.objects.filter(slug=slug).exists():
                self.stdout.write(f"  = {title[:60]}...")
                continue

            post = Post.objects.create(
                title=title[:200],
                slug=slug,
                summary=summary[:1000],
                body=body,
                author=author,
                category=blog_cat,
                firmware_brand=brand,
                firmware_model=device_model,
                status=PostStatus.PUBLISHED,
                is_published=True,
                published_at=now - timedelta(days=random.randint(1, 90)),  # noqa: S311
                reading_time=random.randint(3, 12),  # noqa: S311
                is_ai_generated=True,
            )

            GeneratedFlashingGuide.objects.create(
                template=tmpl,
                post=post,
                brand=brand,
                model=device_model,
                chipset=chipset,
            )

            # Bump template generated_count
            tmpl.generated_count = (tmpl.generated_count or 0) + 1
            tmpl.save(update_fields=["generated_count"])

            created_count += 1
            self.stdout.write(f"  + {title[:60]}...")

        self.stdout.write(self.style.SUCCESS(f"  ✓ {created_count} guides generated"))
        return created_count

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _random_recent_date() -> date:
        days_ago = random.randint(7, 365)  # noqa: S311
        return date.today() - timedelta(days=days_ago)
