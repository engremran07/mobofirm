"""Public API surface for the SEO app, resolved through ``AppService``."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

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


def related_links(obj: Any, limit: int = 8) -> list[dict[str, str]]:
    """Accepted internal links out of ``obj`` (reviewed on the SEO interlinking screen)."""
    from . import interlinking

    return [
        {"url": item.target.url, "anchor": item.anchor or item.target.title}
        for item in interlinking.accepted_links(obj, limit)
    ]


def link_body_html(html: str, obj: Any, max_links: int = 8) -> str:
    """Body HTML with accepted internal links inserted and external links nofollowed."""
    from apps.site_settings.services import site_origin

    from . import interlinking
    from .services.internal_linking.engine import render_body_with_internal_links

    linked = render_body_with_internal_links(
        body_html=html, source_obj=obj, max_links=max_links
    )
    host = urlsplit(site_origin()).netloc
    return interlinking.apply_external_link_policy(linked, host) if host else linked


__all__ = ["get_redirects", "get_settings", "link_body_html", "related_links"]
