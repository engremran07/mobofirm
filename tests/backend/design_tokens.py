"""Shared parsing and colour maths for the design-token tests.

The single source of truth for every colour is ``frontend/src/shared/tokens/_palette.scss``;
these helpers read its ``$light`` / ``$dark`` / high-contrast maps so tests never carry
their own copy of a palette.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
PALETTE = FRONTEND / "shared" / "tokens" / "_palette.scss"
EMIT = FRONTEND / "shared" / "tokens" / "_emit.scss"
PUBLIC_TOKENS = FRONTEND / "styles" / "tokens.scss"
CONSOLE_TOKENS = FRONTEND / "admin" / "styles" / "_tokens.scss"

# The only files allowed to contain a raw colour literal.
TOKEN_FILES = frozenset({PALETTE, EMIT})

HEX_VALUE = re.compile(r"^#[0-9a-fA-F]{6}$")


def palette_map(name: str) -> dict[str, str]:
    """Return ``{token: '#rrggbb'}`` for one ``$name: (...)`` map of ``_palette.scss``."""
    scss = PALETTE.read_text(encoding="utf-8")
    match = re.search(rf"\${re.escape(name)}:\s*\((.*?)\n\);", scss, re.DOTALL)
    assert match, f"${name} palette map missing from _palette.scss"
    return {
        key.strip('"'): value.lower()
        for key, value in re.findall(
            r'^\s*("?[a-z-]+"?):\s*(#[0-9a-fA-F]{6}),', match.group(1), re.MULTILINE
        )
    }


def themes() -> dict[str, dict[str, str]]:
    """Resolved palettes: light, dark and their high-contrast variants."""
    light, dark = palette_map("light"), palette_map("dark")
    return {
        "light": light,
        "dark": dark,
        "light-high": {**light, **palette_map("light-high")},
        "dark-high": {**dark, **palette_map("dark-high")},
    }


def _channels(value: str) -> tuple[float, float, float]:
    red, green, blue = (int(value[index : index + 2], 16) / 255 for index in (1, 3, 5))
    return red, green, blue


def luminance(value: str) -> float:
    """WCAG relative luminance of ``#rrggbb``."""
    red, green, blue = (
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in _channels(value)
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` colours."""
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def mix(colour: str, percent: float, base: str) -> str:
    """``color-mix(in srgb, colour percent%, base)`` as ``#rrggbb``."""
    weight = percent / 100
    mixed = (
        round((c * weight + b * (1 - weight)) * 255)
        for c, b in zip(_channels(colour), _channels(base), strict=True)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)
