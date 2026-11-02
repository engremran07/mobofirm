"""Management command to fix ALL SEO issues on blog posts in one sweep.

Fixes:
  - Underscore slugs → hyphens
  - Short SEO titles (<30 chars) → expanded
  - Long SEO titles (>60 chars) → trimmed
  - Short SEO descriptions (<120 chars) → enriched to 120–160 chars
  - Long SEO descriptions (>160 chars) → trimmed to ≤160 chars
  - Thin content (<300 words) → enriched with device-specific sections
  - Missing hero images → set brand-appropriate default
"""

from __future__ import annotations

import logging
import re
import textwrap
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.utils.text import Truncator

from apps.blog.models import Post
from apps.firmwares.tool_matcher import get_tool_list_text, resolve_chipset

logger = logging.getLogger(__name__)

# ── Thresholds (match audit_service.py) ──────────────────────────
TITLE_MIN = 30
TITLE_MAX = 60
DESC_MIN = 120
DESC_MAX = 160
CONTENT_MIN_WORDS = 300

# Default hero image (firmware-themed placeholder)
DEFAULT_HERO = "/static/img/firmware-hero-default.webp"


class Command(BaseCommand):
    help = "Fix all SEO issues on blog posts: slugs, titles, descriptions, content, images."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without saving.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run: bool = options["dry_run"]
        posts = Post.objects.all().select_related(
            "category", "firmware_brand", "firmware_model"
        )
        total = posts.count()
        self.stdout.write(f"\nAuditing {total} posts for SEO issues…\n")

        stats: dict[str, int] = {
            "slug_fixed": 0,
            "title_fixed": 0,
            "desc_fixed": 0,
            "body_enriched": 0,
            "hero_set": 0,
        }

        for post in posts:
            changed = False
            brand_name = _get_brand_name(post)
            model_name = _get_model_name(post)
            device = f"{brand_name} {model_name}".strip() or "your device"

            # 1) Fix underscore slugs
            if "_" in (post.slug or ""):
                old_slug = post.slug
                post.slug = post.slug.replace("_", "-")
                self._log(f"  [slug] {old_slug} → {post.slug}", post)
                stats["slug_fixed"] += 1
                changed = True

            # 2) Fix short SEO titles
            seo_title = post.seo_title or post.title or ""
            if len(seo_title) < TITLE_MIN:
                new_title = _expand_title(seo_title, brand_name, model_name)
                post.seo_title = new_title
                self._log(f"  [title] expanded: {new_title}", post)
                stats["title_fixed"] += 1
                changed = True

            # 3) Fix long SEO titles
            elif len(seo_title) > TITLE_MAX:
                new_title = _shorten_title(seo_title)
                post.seo_title = new_title
                self._log(f"  [title] trimmed: {new_title}", post)
                stats["title_fixed"] += 1
                changed = True

            # 4) Fix short descriptions
            seo_desc = post.seo_description or ""
            if 0 < len(seo_desc) < DESC_MIN:
                new_desc = _expand_description(seo_desc, device, post)
                post.seo_description = new_desc
                post.summary = new_desc  # keep in sync
                self._log(f"  [desc] expanded to {len(new_desc)}ch", post)
                stats["desc_fixed"] += 1
                changed = True

            # 5) Fix long descriptions
            elif len(seo_desc) > DESC_MAX:
                new_desc = _trim_description(seo_desc)
                post.seo_description = new_desc
                post.summary = new_desc
                self._log(f"  [desc] trimmed to {len(new_desc)}ch", post)
                stats["desc_fixed"] += 1
                changed = True

            # 6) Fix empty descriptions
            elif not seo_desc.strip():
                new_desc = _generate_description(post, device)
                post.seo_description = new_desc
                post.summary = new_desc
                self._log(f"  [desc] generated ({len(new_desc)}ch)", post)
                stats["desc_fixed"] += 1
                changed = True

            # 7) Enrich thin content
            body_words = len((post.body or "").split())
            if body_words < CONTENT_MIN_WORDS:
                category_slug = (post.category.slug if post.category else "") or ""
                enriched = _enrich_body(post.body or "", device, category_slug, post)
                post.body = enriched
                new_count = len(enriched.split())
                self._log(f"  [body] {body_words}w → {new_count}w", post)
                stats["body_enriched"] += 1
                changed = True

            # 8) Set missing hero image
            if not (post.hero_image or "").strip():
                post.hero_image = DEFAULT_HERO
                self._log("  [hero] set default image", post)
                stats["hero_set"] += 1
                changed = True

            if changed and not dry_run:
                post.save(
                    update_fields=[
                        "slug",
                        "seo_title",
                        "seo_description",
                        "summary",
                        "body",
                        "hero_image",
                    ]
                )

        # Summary
        self.stdout.write("")
        label = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{label}SEO fix complete:\n"
                f"  Slugs fixed:       {stats['slug_fixed']}\n"
                f"  Titles fixed:      {stats['title_fixed']}\n"
                f"  Descriptions fixed: {stats['desc_fixed']}\n"
                f"  Bodies enriched:   {stats['body_enriched']}\n"
                f"  Hero images set:   {stats['hero_set']}"
            )
        )

    def _log(self, msg: str, post: Post) -> None:
        self.stdout.write(f"Post #{post.pk} ({post.title[:40]}…): {msg}")


# ── Helper Functions ─────────────────────────────────────────────


def _get_brand_name(post: Post) -> str:
    if post.firmware_brand:
        return post.firmware_brand.name  # type: ignore[attr-defined]
    # Extract from title
    title = post.title or ""
    for prefix in ("How to Flash Stock Firmware on ", "Root ", "Unlock Bootloader on "):
        if title.startswith(prefix):
            rest = title[len(prefix) :].split("—")[0].strip()
            return rest.split()[0] if rest else ""
    return title.split("(")[0].split("—")[0].strip().split()[0] if title else ""


def _get_model_name(post: Post) -> str:
    if post.firmware_model:
        return post.firmware_model.name  # type: ignore[attr-defined]
    title = post.title or ""
    # Extract device name from parentheses or before em-dash
    before_dash = title.split("—")[0].strip()
    # Remove common prefixes
    for prefix in (
        "How to Flash Stock Firmware on ",
        "Root ",
        "Unlock Bootloader on ",
        "Install Custom ROM on ",
        "Downgrade ",
        "Unbrick ",
        "FRP Bypass for ",
        "Install TWRP Recovery on ",
    ):
        if before_dash.startswith(prefix):
            return before_dash[len(prefix) :].strip()
    # For firmware download posts: "Brand Model Firmware Download"
    cleaned = re.sub(r"\s*\(.*?\)", "", before_dash)
    cleaned = re.sub(r"\s*Firmware\s+Download.*$", "", cleaned)
    return cleaned.strip()


def _expand_title(title: str, brand: str, model: str) -> str:
    """Expand short titles to ≥30 chars."""
    if not title:
        return f"{brand} {model} — Firmware & Guides".strip()[:TITLE_MAX]
    suffixes = [
        " — All Variants & Regions",
        f" — {brand} Official Files",
        " — Download & Install Guide",
    ]
    for suffix in suffixes:
        candidate = title + suffix
        if TITLE_MIN <= len(candidate) <= TITLE_MAX:
            return candidate
    return (title + " — All Variants & Regions")[:TITLE_MAX]


def _shorten_title(title: str) -> str:
    """Trim long titles to ≤60 chars at a natural break."""
    # Try removing common suffixes first
    for suffix in (
        " — Step-by-Step Guide",
        " — Official Method",
        " — Google Account Lock Removal",
        " — Roll Back to Previous Version",
        " — Complete Guide",
    ):
        if title.endswith(suffix):
            core = title[: -len(suffix)]
            if len(core) <= TITLE_MAX:
                return core
    # Truncate on word boundary
    return Truncator(title).chars(TITLE_MAX, truncate="")


def _expand_description(desc: str, device: str, post: Post) -> str:
    """Expand short description to 120–160 chars with relevant context."""
    category_slug = (post.category.slug if post.category else "") or ""

    suffixes_by_type: dict[str, list[str]] = {
        "firmware": [
            f" Official stock ROM download with step-by-step install guide for {device}.",
            f" Free download for all {device} variants. Includes install instructions and driver links.",
            f" Compatible with all {device} regions and carriers. Updated firmware catalog.",
        ],
        "flashing-guides": [
            f" Includes required tools, driver setup, and troubleshooting for {device}.",
            " Detailed walkthrough with prerequisites, backup steps, and verified methods.",
            " Safe method with full prerequisites and post-flash verification steps.",
        ],
    }

    hints = suffixes_by_type.get(category_slug, suffixes_by_type["firmware"])

    for hint in hints:
        candidate = desc.rstrip(".") + "." + hint
        if DESC_MIN <= len(candidate) <= DESC_MAX:
            return candidate

    # Fallback: pad to minimum
    base = desc.rstrip(".")
    extra = f" Download, install, and troubleshoot {device} firmware with our verified guides and tools."
    candidate = base + "." + extra
    return candidate[:DESC_MAX]


def _trim_description(desc: str) -> str:
    """Trim description to ≤160 chars at word boundary."""
    return Truncator(desc).chars(DESC_MAX, truncate="…")


def _generate_description(post: Post, device: str) -> str:
    """Generate description from scratch when none exists."""
    return (
        f"Download and install firmware for {device}. "
        f"Official stock ROM files, flashing guides, and driver links. "
        f"Compatible with all variants and regions."
    )[:DESC_MAX]


def _enrich_body(body: str, device: str, category_slug: str, post: Post) -> str:
    """Add SEO-rich sections to reach 300+ words."""
    brand = _get_brand_name(post)

    if category_slug == "flashing-guides":
        extra = _guide_enrichment(device, brand, post)
    else:
        extra = _firmware_enrichment(device, brand, post)

    enriched = body.rstrip() + "\n\n" + extra

    # Ensure we hit 300 words
    if len(enriched.split()) < CONTENT_MIN_WORDS:
        enriched += "\n\n" + _generic_faq(device, brand)

    return enriched


def _firmware_enrichment(device: str, brand: str, post: Post) -> str:
    """Extra content sections for firmware download posts."""
    model_name = _get_model_name(post)
    chipset = resolve_chipset(brand, model_name)
    tool_text = get_tool_list_text(brand, chipset)

    return textwrap.dedent(f"""\
        <h2>How to Install {device} Firmware</h2>
        <p>Before installing firmware on your <strong>{device}</strong>, ensure your device battery \
        is charged to at least 50%. Back up all important data including contacts, photos, and app data \
        as the flashing process may wipe your device storage. A stable internet connection is required \
        to download the firmware files and USB drivers.</p>

        <h3>Installation Requirements</h3>
        <ul>
        <li>Compatible USB cable (original {brand} cable recommended)</li>
        <li>Latest USB drivers for {device} installed on your computer</li>
        <li>Flash tool compatible with your {device} chipset ({tool_text})</li>
        <li>Minimum 2 GB free disk space for firmware extraction</li>
        <li>Windows 7 or later PC (64-bit recommended)</li>
        </ul>

        <h3>Step-by-Step Installation</h3>
        <ol>
        <li>Download the correct firmware file for your exact {device} model variant and region.</li>
        <li>Extract the downloaded archive using 7-Zip or WinRAR to a folder on your desktop.</li>
        <li>Install the required USB drivers on your computer and restart if prompted.</li>
        <li>Open the appropriate flash tool and load the firmware scatter file or configuration.</li>
        <li>Power off your {device} completely and connect it to your computer via USB while holding the \
correct button combination for download mode.</li>
        <li>Click the Download or Start button in the flash tool and wait for the process to complete.</li>
        <li>Once the flash tool shows a success indicator, disconnect the USB cable and power on your device.</li>
        </ol>

        <h3>After Installation</h3>
        <p>The first boot after a firmware flash may take 5 to 10 minutes. Do not interrupt this process. \
Once the device boots to the home screen, verify the firmware version in Settings → About Phone → \
Software Information. If you experience any issues, try a factory reset from recovery mode.</p>

        <h3>Compatibility Notice</h3>
        <p>Always download firmware matching your exact {device} model number and region code. Installing \
firmware intended for a different variant may cause boot loops, lost IMEI, or hardware damage. \
Check the model number on the sticker under the battery or in Settings → About Phone before \
proceeding. {brand} firmware files are regularly updated as new versions become available.</p>
    """)


def _guide_enrichment(device: str, brand: str, post: Post) -> str:
    """Extra content sections for flashing guide posts."""
    title = post.title or ""

    # Detect guide type from title
    if "root" in title.lower():
        topic_verb = "rooting"
        topic_noun = "root access"
    elif "bootloader" in title.lower():
        topic_verb = "bootloader unlocking"
        topic_noun = "an unlocked bootloader"
    elif "custom rom" in title.lower():
        topic_verb = "custom ROM installation"
        topic_noun = "a custom ROM"
    elif "unbrick" in title.lower() or "repair" in title.lower():
        topic_verb = "unbricking"
        topic_noun = "brick recovery"
    elif "frp" in title.lower():
        topic_verb = "FRP bypass"
        topic_noun = "FRP lock removal"
    elif "recovery" in title.lower() or "twrp" in title.lower():
        topic_verb = "custom recovery installation"
        topic_noun = "TWRP recovery"
    elif "downgrade" in title.lower():
        topic_verb = "firmware downgrading"
        topic_noun = "a firmware downgrade"
    else:
        topic_verb = "stock firmware flashing"
        topic_noun = "stock firmware"

    html = f"""\
        <h3>Important Safety Notes</h3>
        <p>Performing {topic_verb} on your <strong>{device}</strong> carries certain risks. \
        Always ensure you have a complete backup of your data before starting. This includes contacts, \
        photos, app data, and any files stored on internal storage. We recommend using {brand}'s official \
        backup tool or a third-party solution like Titanium Backup or Swift Backup for rooted devices.</p>

        <h3>Troubleshooting Common Issues</h3>
        <ul>
        <li><strong>Device not detected by PC:</strong> Reinstall USB drivers, try a different USB port \
        or cable, and ensure USB debugging is enabled in Developer Options.</li>
        <li><strong>Flash tool shows error:</strong> Verify the firmware file integrity by checking the \
        MD5 hash. Re-download if the hash does not match. Ensure no antivirus software is blocking the tool.</li>
        <li><strong>Device stuck on boot logo:</strong> Perform a factory reset from recovery mode by \
        holding Volume Up + Power. Select "Wipe data/factory reset" and reboot.</li>
        <li><strong>Lost IMEI after flash:</strong> This usually means an incorrect firmware variant was \
        used. Re-flash with the correct region-specific firmware to restore the IMEI.</li>
        </ul>

        <h3>Frequently Asked Questions</h3>
        <p><strong>Will this void my warranty?</strong><br>
        Yes, {topic_verb} typically voids the manufacturer warranty on {brand} devices. However, \
        restoring stock firmware may reinstate warranty eligibility in some regions.</p>

        <p><strong>Is it safe to perform {topic_verb}?</strong><br>
        When following the correct steps with verified firmware files, {topic_verb} is generally safe. \
        The main risk comes from using incorrect firmware for your device variant or interrupting the \
        process. Always verify your model number before proceeding.</p>

        <p><strong>Can I revert back?</strong><br>
        In most cases, you can restore your {device} to stock firmware by following our stock firmware \
        flashing guide. Having {topic_noun} does not permanently alter your device hardware.</p>

        <p><strong>What tools do I need?</strong><br>
        You will need a Windows PC with the correct USB drivers installed, the appropriate flash tool for \
        your device chipset, and the firmware file downloaded from this page. A quality USB cable is \
        essential for a stable connection during the flash process.</p>
    """  # noqa: S608
    return textwrap.dedent(html)


def _generic_faq(device: str, brand: str) -> str:
    """Generic FAQ to pad word count when needed."""
    return textwrap.dedent(f"""\
        <h3>Additional Information</h3>
        <p>The firmware files available on this page are sourced from official {brand} servers and \
        verified for integrity. Each firmware package includes the system image, bootloader, modem \
        files, and recovery partition required for a complete installation. We recommend checking \
        back regularly as {brand} releases periodic security patches and feature updates for the \
        {device}.</p>

        <p>For best results, always use the latest version of the flash tool recommended for your \
        device chipset. Ensure your computer has a stable power supply during the flashing process, \
        as an unexpected shutdown could leave your device in an unbootable state. If you encounter \
        any difficulties, our community forum has dedicated sections for {brand} device support \
        where experienced users can help troubleshoot specific issues.</p>
    """)
