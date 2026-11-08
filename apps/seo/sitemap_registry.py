"""Single sitemap registry for the project.

Each installed domain app contributes its own ``Sitemap`` class; the registry maps
the public section name to that class. Apps that are not installed are skipped, and
other code can add sections at runtime with :func:`register_sitemap`.

Sitemap membership is reconciled with indexation policy: an app may register a
*filter* for its section (see :func:`register_sitemap_filter`) that drops items the
page resolver would mark ``noindex`` or canonicalise elsewhere, so the sitemap can
never contradict the page head.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.apps import apps
from django.contrib.sitemaps import Sitemap
from django.utils.module_loading import import_string

from .models import SEOSettings

DEFAULT_SECTIONS: dict[str, tuple[str, str]] = {
    "static": ("apps.pages", "apps.pages.sitemaps.StaticViewsSitemap"),
    "pages": ("apps.pages", "apps.pages.sitemaps.PublishedPagesSitemap"),
    "blog": ("apps.blog", "apps.blog.sitemaps.PublishedBlogPostsSitemap"),
    "brands": ("apps.firmwares", "apps.firmwares.sitemaps.FirmwareBrandsSitemap"),
    "chipsets": ("apps.firmwares", "apps.firmwares.sitemaps.FirmwareChipsetsSitemap"),
    "models": ("apps.firmwares", "apps.firmwares.sitemaps.FirmwareModelsSitemap"),
    "firmware": ("apps.firmwares", "apps.firmwares.sitemaps.FirmwareFileSitemap"),
    "tools": ("apps.firmwares", "apps.firmwares.sitemaps.FirmwareToolsSitemap"),
    "tags": ("apps.tags", "apps.tags.sitemaps.PublishedTagsSitemap"),
}

SitemapFilter = Callable[[list[Any], SEOSettings], list[Any]]

_extra_sections: dict[str, type[Sitemap]] = {}
_filters: dict[str, SitemapFilter] = {}


def register_sitemap(name: str, sitemap_class: type[Sitemap]) -> None:
    """Add or replace a sitemap section at runtime."""
    if not name or "/" in name or not issubclass(sitemap_class, Sitemap):
        raise ValueError(f"Invalid sitemap registration: {name!r}")
    _extra_sections[name] = sitemap_class


def unregister_sitemap(name: str) -> None:
    _extra_sections.pop(name, None)


def register_sitemap_filter(section: str, filter_fn: SitemapFilter) -> None:
    """Keep only the items of ``section`` that policy says are indexable."""
    _filters[section] = filter_fn


def _reconciled(section: str, sitemap_class: type[Sitemap]) -> type[Sitemap]:
    filter_fn = _filters.get(section)

    def items(self: Sitemap) -> list[Any]:
        listed = list(sitemap_class.items(self))
        return filter_fn(listed, SEOSettings.get_solo()) if filter_fn else listed

    def get_protocol(_self: Sitemap, protocol: str | None = None) -> str:
        # The public scheme comes from SITE_URL, never from a per-app guess.
        return protocol or "https"

    return type(
        sitemap_class.__name__,
        (sitemap_class,),
        {"items": items, "get_protocol": get_protocol},
    )


def get_raw_sitemaps() -> dict[str, type[Sitemap]]:
    """Every registered section without indexation filters (the full public set)."""
    sections: dict[str, type[Sitemap]] = {}
    for name, (app_name, dotted_path) in DEFAULT_SECTIONS.items():
        if apps.is_installed(app_name):
            sections[name] = import_string(dotted_path)
    sections.update(_extra_sections)
    return sections


def get_sitemaps() -> dict[str, type[Sitemap]]:
    return {name: _reconciled(name, cls) for name, cls in get_raw_sitemaps().items()}
