"""Template tags for the devices app — brand logo SVG generation."""

from __future__ import annotations

import hashlib

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

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
    h = int(hashlib.md5(name.lower().encode()).hexdigest()[:8], 16)
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
) -> SafeString:
    """Render an inline SVG brand logo or fall back to an <img> if logo_url exists.

    Usage:
        {% load device_tags %}
        {% brand_logo_svg brand.name size=56 logo_url=brand.logo_url %}
    """
    if logo_url:
        return format_html(
            '<img src="{}" alt="{}" width="{}" height="{}" '
            'class="rounded-lg object-contain {}" loading="lazy" decoding="async" '
            "onerror=\"this.style.display='none';this.nextElementSibling.style.display='flex'\">"
            "{}",
            logo_url,
            brand_name,
            size,
            size,
            extra_class,
            _fallback_svg(brand_name, size, extra_class),
        )
    return _svg_logo(brand_name, size, extra_class)


def _svg_logo(name: str, size: int, extra_class: str) -> SafeString:
    bg = _brand_color(name)[0]
    initials = _brand_initials(name)
    fs = round(size * 0.38)
    r = round(size * 0.167)
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        'viewBox="0 0 {size} {size}" class="rounded-lg shrink-0 {extra_class}" '
        'role="img" aria-label="{name}">'
        '<rect width="{size}" height="{size}" rx="{r}" fill="{bg}"/>'
        '<text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" '
        'fill="white" font-family="Inter,system-ui,sans-serif" '
        'font-weight="700" font-size="{fs}">{initials}</text>'
        "</svg>",
        size=size,
        extra_class=extra_class,
        name=name,
        r=r,
        bg=bg,
        fs=fs,
        initials=initials,
    )


def _fallback_svg(name: str, size: int, extra_class: str) -> SafeString:
    """Hidden SVG fallback shown when logo_url image fails to load."""
    return format_html(
        '<span style="display:none">{}</span>', _svg_logo(name, size, extra_class)
    )
