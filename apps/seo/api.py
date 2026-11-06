"""Public API surface for the SEO app, resolved through ``AppService``."""

from __future__ import annotations

from typing import Any

from apps.seo.models import Redirect, SEOSettings


def get_settings() -> dict[str, Any]:
    settings_row = SEOSettings.get_solo()
    return {
        "seo_enabled": settings_row.seo_enabled,
        "sitemap_enabled": settings_row.sitemap_enabled,
        "auto_meta_enabled": settings_row.auto_meta_enabled,
        "auto_schema_enabled": settings_row.auto_schema_enabled,
        "auto_linking_enabled": settings_row.auto_linking_enabled,
        "autonomy_mode": settings_row.autonomy_mode,
    }


def get_redirects() -> list[dict[str, Any]]:
    return [
        {"from_url": r.source, "to_url": r.target, "is_active": r.is_active}
        for r in Redirect.objects.only("source", "target", "is_active")
    ]


__all__ = ["get_redirects", "get_settings"]
