"""Device catalogue seed data: chipsets, brands, models, flashing tools and guides."""

from __future__ import annotations

from .catalog_1 import BRANDS_PART_1
from .catalog_2 import BRANDS_PART_2
from .catalog_3 import BRANDS_PART_3
from .chipsets import CHIPSETS, CHIPSETS_BY_KEY
from .tools import GUIDES, TOOL_CATEGORIES, TOOLS
from .types import BrandSeed

BRANDS: tuple[BrandSeed, ...] = (*BRANDS_PART_1, *BRANDS_PART_2, *BRANDS_PART_3)

__all__ = [
    "BRANDS",
    "CHIPSETS",
    "CHIPSETS_BY_KEY",
    "GUIDES",
    "TOOLS",
    "TOOL_CATEGORIES",
]
