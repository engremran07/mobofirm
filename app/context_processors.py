import tomllib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from apps.core.models import AppRegistry
from apps.site_settings.services import get_site_settings


@lru_cache(maxsize=1)
def _release_version() -> str:
    try:
        return version("mobofirm")
    except PackageNotFoundError:
        # Not pip-installed (for example a source checkout): pyproject.toml is the
        # single release-version source, so read it instead of failing every page.
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])


def release_metadata(_request) -> dict[str, str]:
    return {"release_version": _release_version()}


# Footer legal pages are CMS pages (apps.pages) published under these slugs.
LEGAL_PAGES = (
    ("Privacy policy", "privacy"),
    ("Terms of service", "terms"),
    ("Cookie and ad consent", "consent"),
)

# Other footer pages are linked only while they are published, so a visitor never
# follows a footer link to a 404.
INFO_PAGES = (
    ("About", "about"),
    ("Contact", "contact"),
    ("FAQ", "faq"),
    ("How to flash", "how-to-flash"),
    ("Firmware disclaimer", "firmware-disclaimer"),
    ("DMCA and takedown", "dmca"),
    ("Advertise", "advertise"),
)

_NAV_NAMESPACES = {
    "blog": "blog",
    "pages": "pages",
    "shop": "shop",
    "marketplace": "marketplace",
    "filemanager": "files",
}

_NAV_SECTIONS = {
    ("firmwares", "library_hub"): "firmware",
    ("firmwares", "browse"): "firmware",
    ("firmwares", "universal_search"): "firmware",
    ("firmwares", "model_detail"): "firmware",
    ("firmwares", "firmware_detail"): "firmware",
    ("firmwares", "brand_list"): "brands",
    ("firmwares", "brand_detail"): "brands",
    ("firmwares", "chipset_list"): "chipsets",
    ("firmwares", "chipset_detail"): "chipsets",
    ("firmwares", "flashing_tools"): "guides",
    ("firmwares", "flashing_tool_detail"): "guides",
    ("firmwares", "flashing_guides"): "guides",
}


def _nav_section(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    if match.namespace in _NAV_NAMESPACES:
        return _NAV_NAMESPACES[match.namespace]
    return _NAV_SECTIONS.get((match.namespace, match.url_name or ""), "")


def _ecosystem_flags() -> dict[str, bool]:
    """Which optional ecosystem apps are switched on in the AppRegistry."""
    names = ("pages", "shop", "marketplace", "filemanager")
    try:
        registry = AppRegistry.get_solo()
    except DatabaseError:
        return dict.fromkeys(names, False)
    return {name: bool(getattr(registry, f"{name}_enabled", False)) for name in names}


def _page_links(
    pages_enabled: bool, catalogue: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    if not pages_enabled:
        return []
    from apps.pages.widgets import published_page_slugs

    try:
        live = published_page_slugs()
    except DatabaseError:
        return []
    return [
        (label, reverse("pages:page", kwargs={"slug": slug}))
        for label, slug in catalogue
        if slug in live
    ]


def site_chrome(request: HttpRequest) -> dict[str, object]:
    """Shared header/footer context: site name, active section, ecosystem links.

    The registry flags and published-page links are lazy so renders that never use
    them (partials, errors) do not touch the database.
    """
    ecosystem = SimpleLazyObject(_ecosystem_flags)
    return {
        "site_name": get_site_settings().site_name,
        "nav_current": _nav_section(request),
        "ecosystem": ecosystem,
        "info_links": SimpleLazyObject(
            lambda: _page_links(ecosystem["pages"], INFO_PAGES)
        ),
        "legal_links": SimpleLazyObject(
            lambda: _page_links(ecosystem["pages"], LEGAL_PAGES)
        ),
    }
