"""Seed all smartphone brands and download SVG logos from Simple Icons CDN.

Creates Brand records in the database for every known smartphone brand,
downloads real SVG logos from Simple Icons CDN, and assigns logo paths.
Falls back to generated placeholder SVGs for brands not in Simple Icons.

Usage:
    python manage.py download_brand_logos --settings=app.settings_dev
    python manage.py download_brand_logos --overwrite --settings=app.settings_dev
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

# Simple Icons CDN base URL
_CDN_BASE = "https://cdn.simpleicons.org"

# ── Comprehensive smartphone brand registry ──────────────────────────
# (name, simple_icons_slug_or_None, website, is_featured, description)
BRANDS: list[tuple[str, str | None, str, bool, str]] = [
    # ── Tier 1 — Global flagships ──
    (
        "Samsung",
        "samsung",
        "https://www.samsung.com",
        True,
        "South Korean multinational electronics corporation, world's largest smartphone manufacturer",
    ),
    (
        "Apple",
        "apple",
        "https://www.apple.com",
        True,
        "American technology company known for iPhone, iOS ecosystem",
    ),
    (
        "Xiaomi",
        "xiaomi",
        "https://www.mi.com",
        True,
        "Chinese electronics company, major global smartphone brand",
    ),
    (
        "Huawei",
        "huawei",
        "https://www.huawei.com",
        True,
        "Chinese multinational technology corporation",
    ),
    (
        "Oppo",
        "oppo",
        "https://www.oppo.com",
        True,
        "Chinese consumer electronics manufacturer",
    ),
    (
        "Vivo",
        "vivo",
        "https://www.vivo.com",
        True,
        "Chinese technology company, BBK Electronics subsidiary",
    ),
    (
        "OnePlus",
        "oneplus",
        "https://www.oneplus.com",
        True,
        "Chinese smartphone manufacturer known for flagship killers",
    ),
    (
        "Google",
        "google",
        "https://store.google.com",
        True,
        "Pixel smartphones with pure Android experience",
    ),
    (
        "Sony",
        "sony",
        "https://www.sony.com",
        True,
        "Japanese multinational, Xperia smartphone line",
    ),
    (
        "Motorola",
        "motorola",
        "https://www.motorola.com",
        True,
        "American telecommunications company, Lenovo subsidiary",
    ),
    (
        "Nokia",
        "nokia",
        "https://www.nokia.com",
        True,
        "Finnish multinational, HMD Global licensed brand",
    ),
    # ── Tier 2 — Major brands ──
    (
        "Realme",
        None,
        "https://www.realme.com",
        True,
        "Chinese smartphone brand targeting young consumers",
    ),
    (
        "Honor",
        "honor",
        "https://www.honor.com",
        True,
        "Chinese smartphone brand, formerly Huawei sub-brand",
    ),
    (
        "Asus",
        "asus",
        "https://www.asus.com",
        True,
        "Taiwanese multinational, ROG Phone gaming series",
    ),
    (
        "Lenovo",
        "lenovo",
        "https://www.lenovo.com",
        True,
        "Chinese multinational, owns Motorola Mobile",
    ),
    (
        "LG",
        "lg",
        "https://www.lg.com",
        False,
        "South Korean electronics company (exited smartphone market 2021)",
    ),
    (
        "HTC",
        "htc",
        "https://www.htc.com",
        False,
        "Taiwanese consumer electronics company",
    ),
    (
        "ZTE",
        None,
        "https://www.ztedevices.com",
        False,
        "Chinese multinational telecommunications equipment company",
    ),
    ("Meizu", None, "https://www.meizu.com", False, "Chinese smartphone manufacturer"),
    (
        "Poco",
        None,
        "https://www.po.co",
        True,
        "Xiaomi sub-brand for performance-focused phones",
    ),
    (
        "Redmi",
        None,
        "https://www.mi.com/redmi",
        True,
        "Xiaomi sub-brand for budget-friendly smartphones",
    ),
    (
        "iQOO",
        None,
        "https://www.iqoo.com",
        False,
        "Vivo sub-brand for performance/gaming smartphones",
    ),
    (
        "Nothing",
        None,
        "https://nothing.tech",
        True,
        "British technology company by Carl Pei",
    ),
    (
        "Tecno",
        None,
        "https://www.tecno-mobile.com",
        True,
        "Transsion Holdings brand popular in Africa",
    ),
    (
        "Infinix",
        None,
        "https://www.infinixmobility.com",
        True,
        "Transsion Holdings brand for young consumers",
    ),
    (
        "Itel",
        None,
        "https://www.itel-mobile.com",
        False,
        "Transsion Holdings budget brand",
    ),
    # ── Tier 3 — Regional / Niche ──
    (
        "Alcatel",
        None,
        "https://www.alcatelmobile.com",
        False,
        "TCL-licensed French-origin mobile phone brand",
    ),
    (
        "TCL",
        None,
        "https://www.tcl.com",
        False,
        "Chinese electronics company, makes Alcatel-branded phones",
    ),
    (
        "BlackBerry",
        "blackberry",
        "https://www.blackberry.com",
        False,
        "Canadian company, once dominant in enterprise smartphones",
    ),
    (
        "Sharp",
        "sharp",
        "https://www.sharp.co.jp",
        False,
        "Japanese electronics company, AQUOS phone line",
    ),
    (
        "Panasonic",
        "panasonic",
        "https://www.panasonic.com",
        False,
        "Japanese multinational electronics corporation",
    ),
    (
        "Kyocera",
        None,
        "https://www.kyocera.com",
        False,
        "Japanese electronics company, rugged phones",
    ),
    (
        "Coolpad",
        None,
        "https://www.coolpad.com",
        False,
        "Chinese smartphone manufacturer",
    ),
    (
        "Gionee",
        None,
        "https://www.gionee.com",
        False,
        "Chinese smartphone manufacturer",
    ),
    (
        "Micromax",
        None,
        "https://www.micromaxinfo.com",
        False,
        "Indian consumer electronics company",
    ),
    (
        "Lava",
        None,
        "https://www.lavamobiles.com",
        False,
        "Indian multinational mobile phone company",
    ),
    (
        "Karbonn",
        None,
        "https://www.karbonnmobiles.com",
        False,
        "Indian smartphone manufacturer",
    ),
    ("Intex", None, "https://www.intex.in", False, "Indian electronics company"),
    (
        "BLU",
        None,
        "https://www.bluproducts.com",
        False,
        "American mobile phone brand, Bold Like Us",
    ),
    (
        "Wiko",
        None,
        "https://www.wikomobile.com",
        False,
        "French-Chinese smartphone manufacturer",
    ),
    ("BQ", None, "https://www.bq.com", False, "Spanish electronics company"),
    # ── Gaming / Performance ──
    (
        "Razer",
        "razer",
        "https://www.razer.com",
        False,
        "American gaming hardware company",
    ),
    (
        "Nubia",
        None,
        "https://www.nubia.com",
        False,
        "ZTE sub-brand for gaming/camera phones",
    ),
    (
        "Red Magic",
        None,
        "https://www.redmagic.gg",
        False,
        "Nubia gaming smartphone brand",
    ),
    (
        "Black Shark",
        None,
        "https://www.blackshark.com",
        False,
        "Xiaomi-backed gaming smartphone brand",
    ),
    (
        "ROG Phone",
        None,
        "https://rog.asus.com",
        False,
        "Asus Republic of Gamers smartphone line",
    ),
    # ── Chinese ODM / Value brands ──
    (
        "LeEco",
        None,
        "https://www.leeco.com",
        False,
        "Chinese conglomerate, formerly LeTV",
    ),
    ("Cubot", None, "https://www.cubot.net", False, "Chinese smartphone manufacturer"),
    ("Umidigi", None, "https://www.umidigi.com", False, "Chinese smartphone brand"),
    (
        "Doogee",
        None,
        "https://www.doogee.cc",
        False,
        "Chinese rugged & budget smartphone brand",
    ),
    (
        "Oukitel",
        None,
        "https://www.oukitel.com",
        False,
        "Chinese rugged smartphone manufacturer",
    ),
    (
        "Ulefone",
        None,
        "https://www.ulefone.com",
        False,
        "Chinese rugged smartphone brand",
    ),
    (
        "Blackview",
        None,
        "https://www.blackview.hk",
        False,
        "Chinese rugged smartphone manufacturer",
    ),
    (
        "Elephone",
        None,
        "https://www.elephone.hk",
        False,
        "Chinese budget smartphone brand",
    ),
    ("Vernee", None, "", False, "Chinese smartphone ODM brand"),
    ("Leagoo", None, "", False, "Chinese smartphone manufacturer"),
    ("Homtom", None, "", False, "Chinese budget smartphone brand"),
    (
        "AGM",
        None,
        "https://www.agmmobile.com",
        False,
        "Chinese rugged phone manufacturer",
    ),
    # ── Rugged / Enterprise ──
    (
        "Cat",
        None,
        "https://www.catphones.com",
        False,
        "Caterpillar-licensed rugged smartphones",
    ),
    (
        "RugGear",
        None,
        "https://www.ruggear.com",
        False,
        "German rugged mobile device brand",
    ),
    (
        "Crosscall",
        None,
        "https://www.crosscall.com",
        False,
        "French rugged smartphone brand",
    ),
    (
        "Doro",
        None,
        "https://www.doro.com",
        False,
        "Swedish company making phones for seniors",
    ),
    # ── Japanese market ──
    (
        "Fujitsu",
        "fujitsu",
        "https://www.fujitsu.com",
        False,
        "Japanese ICT company, arrows phone line",
    ),
    ("NEC", "nec", "https://www.nec.com", False, "Japanese multinational IT company"),
    (
        "Toshiba",
        "toshiba",
        "https://www.toshiba.co.jp",
        False,
        "Japanese multinational conglomerate",
    ),
    # ── Korean market ──
    ("Pantech", None, "", False, "South Korean mobile phone manufacturer"),
    # ── Premium / Luxury ──
    (
        "Vertu",
        None,
        "https://www.vertu.com",
        False,
        "British luxury mobile phone manufacturer",
    ),
    (
        "Porsche Design",
        None,
        "https://www.porsche-design.com",
        False,
        "Luxury consumer products brand",
    ),
    # ── Tablet / Multi-device brands ──
    (
        "Amazon",
        "amazon",
        "https://www.amazon.com",
        False,
        "American tech giant, Fire tablets and phones",
    ),
    (
        "Garmin",
        "garmin",
        "https://www.garmin.com",
        False,
        "American multinational, GPS and wearable tech",
    ),
    (
        "Fitbit",
        "fitbit",
        "https://www.fitbit.com",
        False,
        "American wearable technology company, Google subsidiary",
    ),
    # ── Emerging / Startup ──
    (
        "Fairphone",
        "fairphone",
        "https://www.fairphone.com",
        False,
        "Dutch social enterprise making sustainable smartphones",
    ),
    ("Essential", None, "", False, "Andy Rubin's smartphone company (defunct 2020)"),
    # ── Feature phone / Regional ──
    (
        "Symphony",
        None,
        "https://www.symphony-mobile.com",
        False,
        "Bangladeshi mobile phone brand",
    ),
    (
        "Walton",
        None,
        "https://www.waltonbd.com",
        False,
        "Bangladeshi electronics brand",
    ),
    ("Maximus", None, "", False, "Bangladeshi mobile phone brand"),
    ("Spice", None, "https://www.spicemobile.in", False, "Indian mobile phone brand"),
    # ── Tablet-focused ──
    (
        "Chuwi",
        None,
        "https://www.chuwi.com",
        False,
        "Chinese manufacturer of tablets and laptops",
    ),
    (
        "Teclast",
        None,
        "https://www.teclast.com",
        False,
        "Chinese consumer electronics brand",
    ),
    (
        "Alldocube",
        None,
        "https://www.alldocube.com",
        False,
        "Chinese tablet manufacturer",
    ),
    # ── Additional global brands ──
    (
        "Microsoft",
        "microsoft",
        "https://www.microsoft.com",
        False,
        "Surface Duo and Lumia legacy smartphones",
    ),
    ("Palm", None, "", False, "Iconic PDA/smartphone brand (revived 2018)"),
    (
        "Energizer",
        None,
        "https://www.energizermobiles.com",
        False,
        "Licensed brand for rugged/high-capacity phones",
    ),
    (
        "Hisense",
        None,
        "https://www.hisense.com",
        False,
        "Chinese electronics company, e-ink phones",
    ),
    (
        "TCL (Alcatel)",
        None,
        "",
        False,
        "TCL Communication-manufactured Alcatel devices",
    ),
    (
        "Vodafone",
        "vodafone",
        "https://www.vodafone.com",
        False,
        "British MNO with own-brand smartphones",
    ),
    (
        "Philips",
        None,
        "https://www.philips.com",
        False,
        "Dutch company licensing brand for mobile devices",
    ),
    ("Acer", "acer", "https://www.acer.com", False, "Taiwanese electronics company"),
    (
        "HMD Global",
        None,
        "https://www.hmd.com",
        False,
        "Finnish company manufacturing Nokia-branded phones",
    ),
    ("Wileyfox", None, "", False, "British smartphone manufacturer (defunct)"),
    (
        "Gigaset",
        None,
        "https://www.gigaset.com",
        False,
        "German telecommunications company",
    ),
    (
        "Condor",
        None,
        "https://www.condor.dz",
        False,
        "Algerian electronics manufacturer",
    ),
    (
        "Positivo",
        None,
        "https://www.positivotecnologia.com.br",
        False,
        "Brazilian electronics company",
    ),
    (
        "Multilaser",
        None,
        "https://www.multilaser.com.br",
        False,
        "Brazilian consumer electronics brand",
    ),
    ("Fly", None, "", False, "Russian-origin mobile phone brand"),
    (
        "Yandex",
        "yandex",
        "https://www.yandex.com",
        False,
        "Russian technology company, Yandex.Phone",
    ),
    (
        "General Mobile",
        None,
        "https://www.generalmobile.com",
        False,
        "Turkish smartphone manufacturer",
    ),
    (
        "Vestel",
        None,
        "https://www.vestel.com.tr",
        False,
        "Turkish consumer electronics company",
    ),
    (
        "Cherry Mobile",
        None,
        "https://www.cherrymobile.com.ph",
        False,
        "Filipino mobile phone brand",
    ),
    (
        "Myphone",
        None,
        "https://www.myphone.com.ph",
        False,
        "Filipino mobile phone brand",
    ),
    ("Opsson", None, "", False, "Chinese smartphone ODM"),
    (
        "Prestigio",
        None,
        "https://www.prestigio.com",
        False,
        "European consumer electronics brand",
    ),
    (
        "Allview",
        None,
        "https://www.allviewmobile.com",
        False,
        "Romanian smartphone brand",
    ),
    ("Mobiistar", None, "", False, "Vietnamese smartphone brand"),
    ("Vsmart", None, "", False, "Vietnamese smartphone brand by Vingroup"),
    ("Bphone", None, "", False, "Vietnamese premium smartphone brand by BKAV"),
]

# Placeholder palette for brands without Simple Icons entry
_BRAND_COLORS = [
    ("#06b6d4", "#083344"),  # cyan
    ("#8b5cf6", "#1e1b4b"),  # violet
    ("#f59e0b", "#451a03"),  # amber
    ("#10b981", "#064e3b"),  # emerald
    ("#ef4444", "#450a0a"),  # red
    ("#3b82f6", "#172554"),  # blue
    ("#ec4899", "#500724"),  # pink
    ("#14b8a6", "#042f2e"),  # teal
    ("#f97316", "#431407"),  # orange
    ("#a855f7", "#2e1065"),  # purple
    ("#22d3ee", "#083344"),  # sky
    ("#84cc16", "#1a2e05"),  # lime
]


def _brand_color(name: str) -> str:
    h = int(hashlib.md5(name.lower().encode()).hexdigest()[:8], 16)  # noqa: S324
    return _BRAND_COLORS[h % len(_BRAND_COLORS)][0]


def _brand_initials(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def _generate_placeholder_svg(name: str) -> str:
    """Generate a placeholder SVG with brand initials."""
    bg = _brand_color(name)
    initials = _brand_initials(name)
    size = 128
    fs = round(size * 0.38)
    r = round(size * 0.167)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}">\n'
        f'  <rect width="{size}" height="{size}" rx="{r}" fill="{bg}"/>\n'
        f'  <text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" '
        f'fill="white" font-family="Inter,system-ui,sans-serif" '
        f'font-weight="700" font-size="{fs}">{initials}</text>\n'
        f"</svg>\n"
    )


def _download_simple_icon(slug: str) -> str | None:
    """Download SVG from Simple Icons CDN. Returns SVG string or None."""
    url = f"{_CDN_BASE}/{slug}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200 and resp.text.strip().startswith("<svg"):
            return resp.text
    except requests.RequestException:
        pass
    return None


class Command(BaseCommand):
    help = "Seed all smartphone brands into DB and download SVG logos"

    def add_arguments(self, parser):  # type: ignore[override]
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing SVG files",
        )
        parser.add_argument(
            "--placeholder-only",
            action="store_true",
            help="Only generate placeholders, skip CDN downloads",
        )
        parser.add_argument(
            "--logos-only",
            action="store_true",
            help="Only download/generate logos, skip DB creation",
        )

    def handle(self, *args: object, **options: object) -> None:
        from django.utils.text import slugify

        from apps.firmwares.models import Brand

        out_dir = Path(settings.BASE_DIR) / "static" / "img" / "brands"
        out_dir.mkdir(parents=True, exist_ok=True)

        overwrite = bool(options.get("overwrite"))
        placeholder_only = bool(options.get("placeholder_only"))
        logos_only = bool(options.get("logos_only"))

        created_db = 0
        updated_db = 0
        downloaded = 0
        placeholders = 0
        skipped = 0

        self.stdout.write(f"Processing {len(BRANDS)} brands...\n")

        for name, icon_slug, website, is_featured, description in BRANDS:
            file_slug = slugify(name)

            # ── Step 1: Create/update Brand in DB ──
            if not logos_only:
                brand, was_created = Brand.objects.get_or_create(
                    slug=file_slug,
                    defaults={
                        "name": name,
                        "website_url": website,
                        "is_featured": is_featured,
                        "description": description,
                    },
                )
                if was_created:
                    created_db += 1
                else:
                    # Update fields that are empty
                    changed = False
                    if not brand.description and description:
                        brand.description = description
                        changed = True
                    if not brand.website_url and website:
                        brand.website_url = website
                        changed = True
                    if changed:
                        brand.save(update_fields=["description", "website_url"])
                        updated_db += 1

            # ── Step 2: Download / generate logo SVG ──
            svg_path = out_dir / f"{file_slug}.svg"

            if svg_path.exists() and not overwrite:
                skipped += 1
                continue

            svg_content: str | None = None

            if icon_slug and not placeholder_only:
                self.stdout.write(f"  Downloading {name} ({icon_slug})...")
                svg_content = _download_simple_icon(icon_slug)
                if svg_content:
                    downloaded += 1
                    self.stdout.write(
                        self.style.SUCCESS(f"  \u2713 {name} \u2192 downloaded")
                    )
                    time.sleep(0.3)
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  \u2717 {name} \u2192 not found, using placeholder"
                        )
                    )

            if not svg_content:
                svg_content = _generate_placeholder_svg(name)
                placeholders += 1
                self.stdout.write(f"  \u25cb {name} \u2192 placeholder")

            svg_path.write_text(svg_content, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {created_db} brands created, {updated_db} updated, "
                f"{downloaded} logos downloaded, {placeholders} placeholders, "
                f"{skipped} skipped"
            )
        )
        self.stdout.write(f"Output: {out_dir}")
