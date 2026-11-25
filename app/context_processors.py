"""Template context values for shared public-site chrome."""

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from app.version import BuildInfo, build_info
from apps.core.models import AppRegistry
from apps.pages.widgets import published_page_slugs
from apps.site_settings.services import get_site_settings


def release_metadata(_request: HttpRequest | None) -> dict[str, str | BuildInfo]:
    """Release version (single source: ``app.version``) and build metadata."""
    info = build_info()
    return {"release_version": info.version, "release_build": info}


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
    "bounty": "bounty",
    "gamification": "gamification",
    "pages": "pages",
    "shop": "shop",
    "marketplace": "marketplace",
    "library": "contributions",
    "contributions": "contributions",
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
    names = (
        "blog",
        "pages",
        "shop",
        "marketplace",
        "wallet",
        "referral",
        "consent",
        "bounty",
        "gamification",
    )
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

    def public_sitemap() -> bool:
        from apps.core.app_service import AppService

        contract = AppService.get("seo")
        if contract is None:
            return False
        values = contract.get_settings()
        return bool(values.get("seo_enabled") and values.get("sitemap_enabled"))

    return {
        "site_name": get_site_settings().site_name,
        "nav_current": _nav_section(request),
        "ecosystem": ecosystem,
        "public_sitemap_enabled": SimpleLazyObject(public_sitemap),
        "info_links": SimpleLazyObject(
            lambda: _page_links(ecosystem["pages"], INFO_PAGES)
        ),
        "legal_links": SimpleLazyObject(
            lambda: _page_links(ecosystem["pages"], LEGAL_PAGES)
        ),
    }
