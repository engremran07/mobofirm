from __future__ import annotations

import functools
from typing import Any

from django.conf import settings

from apps.core.app_service import AppService


@functools.lru_cache(maxsize=1)
def get_settings() -> Any | None:
    """
    Small, process-local cache to avoid hitting the DB on every flag check.
    Uses lazy import to avoid circular dependency.
    """
    return settings


def _seo_setting(key: str) -> bool:
    """Read the SEO feature flag from the seo app, the single settings authority."""
    seo_api = AppService.get("seo")
    if seo_api is None:
        return False
    return bool(seo_api.get_settings().get(key, False))


def seo_enabled() -> bool:
    return _seo_setting("seo_enabled")


def ads_enabled() -> bool:
    ads_api = AppService.get("ads")
    if ads_api and hasattr(ads_api, "get_settings"):
        return bool(ads_api.get_settings().get("ads_enabled", False))
    return False


def affiliate_enabled() -> bool:
    ads_api = AppService.get("ads")
    if ads_api and hasattr(ads_api, "get_settings"):
        return bool(ads_api.get_settings().get("affiliate_enabled", False))
    return False


def auto_meta_enabled() -> bool:
    return _seo_setting("auto_meta_enabled")


def auto_schema_enabled() -> bool:
    return _seo_setting("auto_schema_enabled")


def auto_linking_enabled() -> bool:
    return _seo_setting("auto_linking_enabled")


def ad_aggressiveness() -> str:
    ads_api = AppService.get("ads")
    if not ads_api:
        return "balanced"
    if hasattr(ads_api, "get_settings"):
        return ads_api.get_settings().get("ad_aggressiveness_level", "balanced")
    return getattr(settings, "AD_AGGRESSIVENESS_LEVEL", "balanced")


def reset_cache() -> None:
    """Used by signals/admin to clear process-local cache after updates."""
    get_settings.cache_clear()
