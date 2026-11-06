"""Public widget data for CMS pages: latest firmware and latest blog posts."""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.urls import reverse

from apps.blog.models import Post
from apps.firmwares.catalog import (
    FIRMWARE_TYPE_DISPLAY,
    PUBLIC_FIRMWARE_TYPES,
    get_public_firmware_queryset,
)
from apps.firmwares.models import FlashingTool

from .models import Page

CACHE_TIMEOUT_SECONDS = 300
LATEST_FIRMWARES_KEY = "pages:widgets:latest_firmwares:{limit}"
LATEST_POSTS_KEY = "pages:widgets:latest_posts:{limit}"
LATEST_TOOLS_KEY = "pages:widgets:latest_tools:{limit}"
PUBLISHED_SLUGS_KEY = "pages:widgets:published_slugs"
CACHED_LIMITS = (3, 4, 6, 8, 10)


def latest_firmwares(limit: int = 8) -> list[dict[str, Any]]:
    """Latest reviewed, active firmware across all typed firmware models."""
    cache_key = LATEST_FIRMWARES_KEY.format(limit=limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    candidates: list[tuple[str, Any]] = []
    for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
        queryset = (
            get_public_firmware_queryset(model_class)
            .select_related("brand", "model")
            .order_by("-created_at")[:limit]
        )
        candidates.extend((type_key, firmware) for firmware in queryset)

    candidates.sort(key=lambda item: item[1].created_at, reverse=True)
    results = [
        {
            "id": str(firmware.pk),
            "type": FIRMWARE_TYPE_DISPLAY[type_key],
            "brand": firmware.brand.name if firmware.brand else "",
            "model": firmware.model.name if firmware.model else "",
            "file_name": firmware.original_file_name,
            "created_at": firmware.created_at,
            "url": reverse(
                "firmwares:firmware_detail",
                kwargs={"firmware_type": type_key, "firmware_id": str(firmware.pk)},
            ),
        }
        for type_key, firmware in candidates[:limit]
    ]
    cache.set(cache_key, results, CACHE_TIMEOUT_SECONDS)
    return results


def latest_posts(limit: int = 6) -> list[dict[str, Any]]:
    """Latest live blog posts (published and not scheduled in the future)."""
    cache_key = LATEST_POSTS_KEY.format(limit=limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    posts = Post.published().order_by("-publish_at")[:limit]
    results = [
        {
            "title": post.title,
            "summary": post.summary,
            "published_at": post.publish_at,
            "url": post.get_absolute_url(),
        }
        for post in posts
    ]
    cache.set(cache_key, results, CACHE_TIMEOUT_SECONDS)
    return results


def latest_tools(limit: int = 6) -> list[dict[str, Any]]:
    """Featured, then alphabetical, active flashing tools."""
    cache_key = LATEST_TOOLS_KEY.format(limit=limit)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    tools = (
        FlashingTool.objects.filter(is_active=True)
        .select_related("category")
        .order_by("-is_featured", "name")[:limit]
    )
    results = [
        {
            "name": tool.name,
            "category": tool.category.name,
            "platform": tool.get_platform_display(),
            "url": reverse(
                "firmwares:flashing_tool_detail", kwargs={"slug": tool.slug}
            ),
        }
        for tool in tools
    ]
    cache.set(cache_key, results, CACHE_TIMEOUT_SECONDS)
    return results


def published_page_slugs() -> frozenset[str]:
    """Slugs of public pages that are live now (used to link only pages that exist)."""
    cached = cache.get(PUBLISHED_SLUGS_KEY)
    if cached is None:
        cached = frozenset(
            Page.objects.published()
            .filter(access_level=Page.Access.PUBLIC)
            .values_list("slug", flat=True)
        )
        cache.set(PUBLISHED_SLUGS_KEY, cached, CACHE_TIMEOUT_SECONDS)
    return cached


def invalidate_widget_caches(*, pages_changed: bool = False) -> None:
    """Drop the homepage widget caches; the page-link cache only follows CMS pages."""
    keys = (
        [LATEST_FIRMWARES_KEY.format(limit=n) for n in CACHED_LIMITS]
        + [LATEST_POSTS_KEY.format(limit=n) for n in CACHED_LIMITS]
        + [LATEST_TOOLS_KEY.format(limit=n) for n in CACHED_LIMITS]
    )
    if pages_changed:
        keys.append(PUBLISHED_SLUGS_KEY)
    cache.delete_many(keys)
