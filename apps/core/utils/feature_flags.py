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


def seo_enabled() -> bool:
    return bool(getattr(settings, "SEO_ENABLED", False))


def ads_enabled() -> bool:
    try:
        ads_api = AppService.get("ads")
        if ads_api and hasattr(ads_api, "get_settings"):
            return bool(ads_api.get_settings().get("ads_enabled", False))
    except Exception:  # noqa: S110
        pass
    return False


def affiliate_enabled() -> bool:
    try:
        ads_api = AppService.get("ads")
        if ads_api and hasattr(ads_api, "get_settings"):
            return bool(ads_api.get_settings().get("affiliate_enabled", False))
    except Exception:  # noqa: S110
        pass
    return False


def auto_meta_enabled() -> bool:
    return bool(getattr(settings, "AUTO_META_ENABLED", False))


def auto_schema_enabled() -> bool:
    return bool(getattr(settings, "AUTO_SCHEMA_ENABLED", False))


def auto_linking_enabled() -> bool:
    return bool(getattr(settings, "AUTO_LINKING_ENABLED", False))


def ad_aggressiveness() -> str:
    try:
        ads_api = AppService.get("ads")
        if not ads_api:
            return "balanced"
        if hasattr(ads_api, "get_settings"):
            return ads_api.get_settings().get("ad_aggressiveness_level", "balanced")
    except Exception:  # noqa: S110
        pass
    return getattr(settings, "AD_AGGRESSIVENESS_LEVEL", "balanced")


def reset_cache() -> None:
    """Used by signals/admin to clear process-local cache after updates."""
    try:
        get_settings.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        return
