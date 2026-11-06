"""
apps.core.context_processors
----------------------------
Context processors.

Goals:
 - Lazy imports for startup/migration safety
 - Template rendering never breaks because of optional lookups (GeoIP, social apps)
 - Zero branding (generic defaults)
 - Site identity and settings live in apps.site_settings (context key ``site_settings``)
 - Hardened region detection + provider resolution
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.http import HttpRequest

from apps.core.utils.ip import get_client_ip

logger = logging.getLogger(__name__)

_DEFAULT_LANGUAGE_REGIONS = {
    "en": "global",
    "ar": "middle_east",
    "zh": "china",
    "ja": "asia",
    "ko": "asia",
    "ru": "russia",
    "de": "europe",
    "fr": "europe",
    "es": "europe",
    "it": "europe",
}


# =====================================================================
# SOCIAL PROVIDER SELECTION (LOCATION AWARE)
# =====================================================================
def _enabled_social_providers() -> list[str]:
    """Providers configured for the current site (all providers if no site)."""
    from allauth.socialaccount.models import SocialApp

    try:
        try:
            current_site: Site | None = Site.objects.get_current()
        except Site.DoesNotExist as exc:
            logger.debug("Site.get_current() failed: %s", exc)
            current_site = None

        apps_qs = (
            SocialApp.objects.filter(sites=current_site)
            if current_site
            else SocialApp.objects.all()
        )
        return list(apps_qs.values_list("provider", flat=True))
    except (DatabaseError, ImproperlyConfigured) as exc:
        logger.debug("SocialApp query failed: %s", exc)
        return []


def location_based_providers(request: HttpRequest) -> dict[str, Any]:
    """
    Returns providers suggested for the user's region, filtered by site's enabled providers.

    Output:
        {
            "location_based_providers": [...],
            "user_region": "...",
            "all_enabled_providers": [...],
        }
    """
    enabled_providers = _enabled_social_providers()
    user_region = detect_user_region(request)

    region_map = getattr(settings, "LOCATION_BASED_PROVIDERS", {}) or {}
    preferred_list = region_map.get(user_region, []) or []

    if not preferred_list:
        preferred_list = getattr(settings, "DEFAULT_SOCIAL_PROVIDERS", ["google"])

    # Only show providers that are configured AND are in the preferred list
    available = (
        [p for p in preferred_list if p in enabled_providers]
        if enabled_providers
        else preferred_list
    )

    # If the resulting list is empty, fall back to all enabled providers
    if not available and enabled_providers:
        available = enabled_providers

    return {
        "location_based_providers": available,
        "user_region": user_region,
        "all_enabled_providers": enabled_providers,
    }


# =====================================================================
# REGION DETECTION
# =====================================================================
def detect_user_region(request: HttpRequest) -> str:
    """
    Hierarchical region detection:
        1) user preference (if authenticated)
        2) GeoIP (if available)
        3) Accept-Language header
        4) fallback = "global"
    """
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        # Assumes the custom User model has a 'preferred_region' field
        pref = getattr(user, "preferred_region", None)
        if pref:
            return str(pref)

    geo_region = _detect_region_via_geoip(request)
    if geo_region:
        return geo_region

    lang_region = _detect_region_via_language(request)
    if lang_region:
        return lang_region

    return "global"


# =====================================================================
# GEOIP REGION
# =====================================================================
def _detect_region_via_geoip(request: HttpRequest) -> str | None:
    geoip_paths_configured = (
        getattr(settings, "GEOIP_PATH", None)
        or getattr(settings, "GEOIP_COUNTRY", None)
        or getattr(settings, "GEOIP_CITY", None)
    )
    if not geoip_paths_configured:
        return None

    ip = get_client_ip(request)
    if not ip or ip in ("127.0.0.1", "::1"):  # Handle both IPv4 and IPv6 localhost
        return None

    # GeoIP2 needs the optional geoip2 package plus GEOS/GDAL libraries.
    try:
        geoip_module = importlib.import_module("django.contrib.gis.geoip2")
        geoip_errors = importlib.import_module("geoip2.errors")
    except (ImportError, ImproperlyConfigured) as exc:
        logger.debug("GeoIP2 unavailable (dependency missing): %s", exc)
        return None

    lookup_errors = (
        ImproperlyConfigured,
        ValueError,
        OSError,
        geoip_errors.GeoIP2Error,
        geoip_module.GeoIP2Exception,
    )
    try:
        country = geoip_module.GeoIP2().country_code(ip)
    except lookup_errors as exc:
        logger.debug("GeoIP failure for %s: %s", ip, exc)
        return None

    if not country:
        return None

    region_map = getattr(settings, "COUNTRY_TO_REGION_MAP", None) or {}
    # Return region from map, or None if no region map entry exists
    return region_map.get(country, None)


# =====================================================================
# LANGUAGE REGION
# =====================================================================
def _detect_region_via_language(request: HttpRequest) -> str | None:
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "") or ""
    if not header:
        return None

    # Extract only the language part (e.g., 'en' from 'en-US,en;q=0.9')
    primary_lang = header.split(",")[0].split(";")[0].split("-")[0].lower()
    if not primary_lang:
        return None

    mapping = (
        getattr(settings, "LANGUAGE_TO_REGION_MAP", None) or _DEFAULT_LANGUAGE_REGIONS
    )

    # Return specific region or fall back to "global" if language is unrecognized
    return mapping.get(primary_lang, "global")


# =====================================================================
# CORE CONTEXT
# =====================================================================
def core_context(request: HttpRequest) -> dict[str, Any]:
    """
    Small, safe global flags for templates.
    """
    return {
        "DEBUG": getattr(settings, "DEBUG", False),
        "ENV": getattr(settings, "ENV", "production"),
        # Use SITE_ID from settings, fallback to 1
        "SITE_ID": getattr(settings, "SITE_ID", 1),
        "TIME_ZONE": getattr(settings, "TIME_ZONE", "UTC"),
    }
