"""Template tags for the devices app — brand logo SVG generation."""

from __future__ import annotations

import hashlib

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Curated palette — high contrast on dark and light backgrounds
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
    """Return a deterministic (bg, text) color pair for a brand name."""
    h = int(hashlib.md5(name.lower().encode()).hexdigest()[:8], 16)  # noqa: S324
    return _BRAND_COLORS[h % len(_BRAND_COLORS)]


def _brand_initials(name: str) -> str:
    """Return 1-2 character initials for a brand name."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


@register.simple_tag()
def brand_logo_svg(
    brand_name: str,
    size: int = 48,
    logo_url: str = "",
    extra_class: str = "",
) -> str:
    """Render an inline SVG brand logo or fall back to an <img> if logo_url exists.

    Usage:
        {% load device_tags %}
        {% brand_logo_svg brand.name size=56 logo_url=brand.logo_url %}
    """
    if logo_url:
        return mark_safe(  # noqa: S308 — controlled template tag, ORM data only
            f'<img src="{logo_url}" alt="{brand_name}" '
            f'width="{size}" height="{size}" '
            f'class="rounded-lg object-contain {extra_class}" '
            f'loading="lazy" decoding="async" '
            f"onerror=\"this.style.display='none';this.nextElementSibling.style.display='flex'\">"
            f"{_fallback_svg(brand_name, size, extra_class)}"
        )
    return mark_safe(_svg_logo(brand_name, size, extra_class))  # noqa: S308


def _svg_logo(name: str, size: int, extra_class: str) -> str:
    bg, fg = _brand_color(name)
    initials = _brand_initials(name)
    fs = round(size * 0.38)
    r = round(size * 0.167)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" class="rounded-lg shrink-0 {extra_class}" '
        f'role="img" aria-label="{name}">'
        f'<rect width="{size}" height="{size}" rx="{r}" fill="{bg}"/>'
        f'<text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" '
        f'fill="white" font-family="Inter,system-ui,sans-serif" '
        f'font-weight="700" font-size="{fs}">{initials}</text>'
        f"</svg>"
    )


def _fallback_svg(name: str, size: int, extra_class: str) -> str:
    """Hidden SVG fallback shown when logo_url image fails to load."""
    return f'<span style="display:none">{_svg_logo(name, size, extra_class)}</span>'
