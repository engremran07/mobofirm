"""Generate SVG brand logos for all brands in the database.

Usage:
    python manage.py generate_brand_logos --settings=app.settings_dev
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# Same palette as the template tag for consistency
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


def _brand_color(name: str) -> tuple[str, str]:
    h = int(hashlib.md5(name.lower().encode()).hexdigest()[:8], 16)  # noqa: S324
    return _BRAND_COLORS[h % len(_BRAND_COLORS)]


def _brand_initials(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def _generate_svg(name: str) -> str:
    bg, _ = _brand_color(name)
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


class Command(BaseCommand):
    help = "Generate SVG brand logos for all brands and save to static/img/brands/"

    def add_arguments(self, parser: object) -> None:
        pass

    def handle(self, *args: object, **options: object) -> None:
        from apps.firmwares.models import Brand

        out_dir = Path(settings.BASE_DIR) / "static" / "img" / "brands"
        out_dir.mkdir(parents=True, exist_ok=True)

        brands = Brand.objects.all().order_by("name")
        created = 0

        for brand in brands:
            svg_path = out_dir / f"{brand.slug}.svg"
            svg_content = _generate_svg(brand.name)
            svg_path.write_text(svg_content, encoding="utf-8")
            created += 1
            self.stdout.write(f"  ✓ {brand.name} → {svg_path.name}")

        self.stdout.write(
            self.style.SUCCESS(f"\nGenerated {created} SVG logos in {out_dir}")
        )
