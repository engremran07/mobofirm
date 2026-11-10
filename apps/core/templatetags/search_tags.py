"""Template tags for the unified search: lazily loaded popular blocks."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django import template
from django.urls import reverse

register = template.Library()


@register.inclusion_tag("search/popular_slot.html")
def popular_blocks(
    kinds: str = "model,firmware,blog",
    brand: str = "",
    chipset: str = "",
    heading: str = "",
    layout: str = "row",
) -> dict[str, Any]:
    """Placeholder that loads "Popular models / firmware / articles" over HTMX.

    ``kinds`` is a comma separated list of provider kinds; ``brand`` and
    ``chipset`` (slugs) narrow the lists; ``layout`` is ``row`` (side by side) or
    ``stack`` (one column, for sidebars). The analytics run when the fragment is
    requested (cached), never while the page renders; the fragment is empty when
    there is nothing popular or staff turned the blocks off.
    """
    params = {
        "kinds": kinds,
        "brand": brand,
        "chipset": chipset,
        "heading": heading,
        "layout": layout,
    }
    query = urlencode({key: value for key, value in params.items() if value})
    return {"url": f"{reverse('search:popular')}?{query}"}
