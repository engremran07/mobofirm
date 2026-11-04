"""
apps.core.context_processors
----------------------------
Enterprise-grade context processors.

Goals:
 - Lazy imports for startup/migration safety
 - Fully defensive (never break template rendering)
 - Zero branding (generic defaults)
 - Uses the core settings snapshot and existing app registry
 - Hardened region detection + provider resolution
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest

from apps.core.models import AppRegistry
from apps.core.utils.ip import get_client_ip

logger = logging.getLogger(__name__)


# =====================================================================
# SITE SETTINGS (LIGHT SNAPSHOT)
# =====================================================================
def site_settings_context(request: HttpRequest) -> dict[str, Any]:
    """
    Lightweight supplemental settings for templates.
    The values are derived from the core settings snapshot and existing app registry.
    """

    from apps.core.views import _get_site_settings_snapshot

    snapshot = _get_site_settings_snapshot()

    # 2. Extract values or use defensive defaults
    try:
        reg = AppRegistry.get_solo()
    except Exception:
        reg = None

    return {
        "site_theme": {
            "primary_color": snapshot["primary_color"],
            "secondary_color": snapshot["secondary_color"],
        },
        "site_settings_light": {
            "site_name": snapshot["site_name"],
            "enable_signup": snapshot["enable_signup"],
            "recaptcha_site_key": getattr(settings, "RECAPTCHA_SITE_KEY", ""),
            "recaptcha_enabled": bool(getattr(settings, "RECAPTCHA_ENABLED", False)),
            "show_consent_banner": bool(
                getattr(settings, "CONSENT_BANNER_ENABLED", False)
            ),
        },
        "app_registry": reg,
    }


# =====================================================================
# SOCIAL PROVIDER SELECTION (LOCATION AWARE)
# =====================================================================
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
    enabled_providers: list[str] = []

    # -----------------------------------------------------------------------------
    # Lazy import allauth only if installed
    # -----------------------------------------------------------------------------
    try:
        from allauth.socialaccount.models import SocialApp  # type: ignore
        from django.contrib.sites.models import Site  # type: ignore

        try:
            current_site = Site.objects.get_current()
        except Exception as exc:
            logger.debug("Site.get_current() failed: %s", exc)
            current_site = None

        try:
            if current_site:
                enabled_providers = list(
                    SocialApp.objects.filter(sites=current_site).values_list(
                        "provider", flat=True
                    )
                )
            else:
                # Fallback query for single-site setups if get_current fails defensively
                enabled_providers = list(
                    SocialApp.objects.values_list("provider", flat=True)
                )
        except Exception as exc:
            logger.debug("SocialApp query failed: %s", exc)
            enabled_providers = []

    except Exception:
        # If allauth or sites not installed → gracefully degrade
        enabled_providers = []

    # -----------------------------------------------------------------------------
    # Region detection
    # -----------------------------------------------------------------------------
    try:
        user_region = detect_user_region(request)
    except Exception as exc:
        logger.debug("detect_user_region failed: %s", exc)
        user_region = "global"

    # -----------------------------------------------------------------------------
    # Region → provider mapping
    # -----------------------------------------------------------------------------
    region_map = getattr(settings, "LOCATION_BASED_PROVIDERS", {}) or {}
    preferred_list = region_map.get(user_region, []) or []

    if not preferred_list:
        preferred_list = getattr(settings, "DEFAULT_SOCIAL_PROVIDERS", ["google"])

    # filter down to those enabled on the site
    try:
        # Ensure we only show providers that are configured AND are in the preferred list
        available = (
            [p for p in preferred_list if p in enabled_providers]
            if enabled_providers
            else preferred_list
        )

        # FINAL SANITY CHECK: If the resulting list is empty, fall back to all enabled providers
        if not available and enabled_providers:
            available = enabled_providers

    except Exception:
        available = preferred_list

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

    try:
        user = getattr(request, "user", None)
        if getattr(user, "is_authenticated", False):
            # Assumes the custom User model has a 'preferred_region' field
            pref = getattr(user, "preferred_region", None)
            if pref:
                return str(pref)
    except Exception:  # noqa: S110
        pass

    try:
        geo_region = _detect_region_via_geoip(request)
        if geo_region:
            return geo_region
    except Exception:  # noqa: S110
        pass

    try:
        lang_region = _detect_region_via_language(request)
        if lang_region:
            return lang_region
    except Exception:  # noqa: S110
        pass

    return "global"


# =====================================================================
# GEOIP REGION
# =====================================================================
def _detect_region_via_geoip(request: HttpRequest) -> str | None:
    try:
        geoip_paths_configured = (
            getattr(settings, "GEOIP_PATH", None)
            or getattr(settings, "GEOIP_COUNTRY", None)
            or getattr(settings, "GEOIP_CITY", None)
        )
        if not geoip_paths_configured:
            return None

        try:
            from django.contrib.gis.geoip2 import GeoIP2  # type: ignore
        except Exception as exc:
            logger.debug("GeoIP2 unavailable (dependency missing): %s", exc)
            return None

        ip = _get_client_ip(request)
        if not ip or ip in ("127.0.0.1", "::1"):  # Handle both IPv4 and IPv6 localhost
            return None

        try:
            g = GeoIP2()
            country = g.country_code(ip)
        except Exception as exc:
            logger.debug("GeoIP failure for %s: %s", ip, exc)
            return None

        if not country:
            return None

        region_map = getattr(settings, "COUNTRY_TO_REGION_MAP", None) or {}
        # Return region from map, or None if no region map entry exists
        return region_map.get(country, None)

    except Exception as exc:
        logger.debug("_detect_region_via_geoip unexpected error: %s", exc)
        return None


# =====================================================================
# LANGUAGE REGION
# =====================================================================
def _detect_region_via_language(request: HttpRequest) -> str | None:
    try:
        header = request.META.get("HTTP_ACCEPT_LANGUAGE", "") or ""
        if not header:
            return None

        # Extract only the language part (e.g., 'en' from 'en-US,en;q=0.9')
        primary_lang = header.split(",")[0].split(";")[0].split("-")[0].lower()
        if not primary_lang:
            return None

        mapping = getattr(settings, "LANGUAGE_TO_REGION_MAP", None) or {
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

        # Return specific region or fall back to "global" if language is unrecognized
        return mapping.get(primary_lang, "global")

    except Exception as exc:
        logger.debug("_detect_region_via_language failed: %s", exc)
        return None


# =====================================================================
# IP EXTRACTION (HARDENED)
# =====================================================================
def _get_client_ip(request: HttpRequest) -> str:
    """
    Wrapper around apps.core.utils.ip.get_client_ip for templates.
    Never raises and falls back to loopback on failure.
    """
    try:
        return get_client_ip(request) or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


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
