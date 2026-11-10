"""Curated equivalences between brand, chipset and code-name spellings.

Every group lists terms that mean the same thing to a visitor. A query word that
appears in a group also matches its siblings, so "mtk" finds MediaTek and
"MT6985" finds the Dimensity 9200. The data is static and deliberately small:
only well known vendor abbreviations and SoC part numbers.
"""

from __future__ import annotations

import unicodedata
from typing import Final

SYNONYM_GROUPS: Final[tuple[tuple[str, ...], ...]] = (
    ("mtk", "mediatek"),
    ("qcom", "qualcomm"),
    ("sd", "snapdragon"),
    ("exy", "exynos"),
    ("hisilicon", "kirin"),
    ("mt6985", "dimensity 9200"),
    ("mt6983", "dimensity 9000"),
    ("mt6989", "dimensity 9300"),
    ("mt6893", "dimensity 1200"),
    ("sm8750", "snapdragon 8 elite"),
    ("sm8650", "snapdragon 8 gen 3"),
    ("sm8550", "snapdragon 8 gen 2"),
    ("sm8475", "snapdragon 8+ gen 1"),
    ("sm8450", "snapdragon 8 gen 1"),
    ("sm8350", "snapdragon 888"),
    ("sm8250", "snapdragon 865"),
    ("sm8150", "snapdragon 855"),
)

# Firmware classifications visitors spell in many ways.
FIRMWARE_TYPE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "official": ("stock", "factory", "oem"),
    "engineering": ("eng", "engineer"),
    "readback": ("read back", "dump", "backup"),
    "modified": ("mod", "patched", "custom"),
    "other": ("misc",),
}


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def _build_index() -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for group in SYNONYM_GROUPS:
        folded = [_fold(term) for term in group]
        for term in folded:
            siblings = index.setdefault(term, [])
            siblings.extend(other for other in folded if other != term)
    for canonical, aliases in FIRMWARE_TYPE_ALIASES.items():
        folded = [_fold(canonical), *(_fold(alias) for alias in aliases)]
        for term in folded:
            siblings = index.setdefault(term, [])
            siblings.extend(other for other in folded if other != term)
    return {term: tuple(dict.fromkeys(values)) for term, values in index.items()}


_INDEX: Final[dict[str, tuple[str, ...]]] = _build_index()


def expand(term: str) -> tuple[str, ...]:
    """Folded equivalents of ``term`` (empty when it has none)."""
    return _INDEX.get(_fold(term), ())
