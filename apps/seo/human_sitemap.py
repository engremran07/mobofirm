"""Live, visitor-facing sitemap assembled from public routes and sitemap content."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from django.http import HttpRequest
from django.urls import reverse

from . import sitemap_build
from .inventory import iter_static_routes, resolve_path
from .models import SEOSettings
from .siteurls import site_origin

SECTION_GROUPS = {
    "static": "Explore",
    "brands": "Devices and firmware",
    "chipsets": "Devices and firmware",
    "models": "Devices and firmware",
    "firmware": "Devices and firmware",
    "tools": "Tools and guides",
    "tool-categories": "Tools and guides",
    "blog": "Blog and community",
    "pages": "Help and information",
    "tags": "Blog and community",
}
PAGE_GROUPS = {
    "home": "Explore",
    "brand_list": "Devices and firmware",
    "brand": "Devices and firmware",
    "model": "Devices and firmware",
    "chipset_list": "Devices and firmware",
    "chipset": "Devices and firmware",
    "firmware": "Devices and firmware",
    "firmware_category": "Devices and firmware",
    "firmware_browse": "Devices and firmware",
    "tools_hub": "Tools and guides",
    "tool": "Tools and guides",
    "guides_hub": "Tools and guides",
    "blog_list": "Blog and community",
    "blog_post": "Blog and community",
    "blog_category": "Blog and community",
    "blog_archive": "Blog and community",
    "blog_brand": "Blog and community",
    "blog_model": "Blog and community",
    "tag_list": "Blog and community",
    "tag": "Blog and community",
    "page": "Help and information",
    "pages_index": "Help and information",
    "feeds_index": "Blog and community",
}
GROUP_ORDER = (
    "Explore",
    "Devices and firmware",
    "Tools and guides",
    "Blog and community",
    "Help and information",
)


def _item_label(item: Any, section: str) -> str:
    for attribute in ("title", "display_name", "name", "original_file_name"):
        value = getattr(item, attribute, "")
        if value:
            return str(value)
    if section == "static":
        return str(item)
    return str(item)


def build_human_sitemap(request: HttpRequest) -> list[dict[str, Any]]:
    """Return all unique, canonical indexable route and content URLs by category."""
    grouped: OrderedDict[str, dict[str, dict[str, str]]] = OrderedDict(
        (name, {}) for name in GROUP_ORDER
    )
    seen: set[str] = set()

    def add(path: str, label: str, group: str) -> None:
        normalized = urlsplit(path).path
        if not normalized or normalized in seen or group not in grouped:
            return
        seen.add(normalized)
        grouped[group][normalized] = {
            "label": label,
            "url": path,
        }

    for view_name, path in iter_static_routes():
        if path == reverse("seo_public:human_sitemap"):
            continue
        resolved = resolve_path(path)
        if (
            resolved is None
            or not resolved.indexable
            or urlsplit(resolved.canonical).path != path
        ):
            continue
        group = PAGE_GROUPS.get(resolved.page_type, "Explore")
        add(path, resolved.title or view_name.replace(":", " / "), group)

    origin = site_origin(request)
    for sitemap_file in sitemap_build.catalog(SEOSettings.get_solo(), origin):
        group = SECTION_GROUPS.get(sitemap_file.section, "Explore")
        for entry in sitemap_file.entries:
            path = urlsplit(entry.loc).path
            add(path, _item_label(entry.item, sitemap_file.section), group)

    return [
        {"title": title, "links": list(links.values())}
        for title, links in grouped.items()
        if links
    ]
