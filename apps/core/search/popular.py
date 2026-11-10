"""Popular entries ("Popular models", "Popular firmware", "Popular articles").

Providers rank their own analytics (``SearchProvider.popular``); this module adds
what is common: staff settings, per-kind caching and invalidation. Results are cached
per (kind, scope, window, limit) for the configured minutes and dropped early when a
watched model changes or the settings are saved.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.apps import apps
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_save

from .registry import get_provider
from .types import SearchHit
from .vocabulary import popular_version

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS: Final[int] = 30
DEFAULT_LIMIT: Final[int] = 6
DEFAULT_CACHE_MINUTES: Final[int] = 10
SETTINGS_VERSION_KEY: Final[str] = "search:popular-settings-version"
_SCOPE_KEYS: Final[tuple[str, ...]] = ("brand", "chipset")


@dataclass(frozen=True, slots=True)
class PopularSettings:
    """Effective popularity configuration (defaults when the table is missing)."""

    enabled: bool = True
    window_days: int = DEFAULT_WINDOW_DAYS
    limit: int = DEFAULT_LIMIT
    cache_minutes: int = DEFAULT_CACHE_MINUTES


def popular_settings() -> PopularSettings:
    """Staff-edited settings, or the defaults before the first save or migration."""
    try:
        row = apps.get_model("core", "SearchSettings").objects.first()
    except DatabaseError:
        logger.warning("Search settings unavailable; using defaults")
        return PopularSettings()
    if row is None:
        return PopularSettings()
    return PopularSettings(
        enabled=row.popular_enabled,
        window_days=row.popular_window_days,
        limit=row.popular_limit,
        cache_minutes=row.popular_cache_minutes,
    )


def invalidate_settings_cache(**_kwargs: object) -> None:
    """Forget every cached popular list (the settings changed)."""
    try:
        cache.incr(SETTINGS_VERSION_KEY)
    except ValueError:
        cache.set(SETTINGS_VERSION_KEY, 2, None)


post_save.connect(
    invalidate_settings_cache,
    sender="core.SearchSettings",
    weak=False,
    dispatch_uid="search-popular-settings",
)


def _clean_scope(scope: Mapping[str, str] | None) -> dict[str, str]:
    return {
        key: str(scope[key])[:80] for key in _SCOPE_KEYS if scope and scope.get(key)
    }


def popular_blocks_data(kinds: str, scope: Mapping[str, str]) -> list[dict[str, Any]]:
    """Template data of the popular blocks for comma separated provider ``kinds``."""
    blocks: list[dict[str, Any]] = []
    for kind in (part.strip() for part in kinds.split(",") if part.strip()):
        provider = get_provider(kind)
        if provider is None or not provider.popular_title:
            continue
        hits = popular_entries(kind, scope=scope)
        if hits:
            blocks.append(
                {
                    "kind": kind,
                    "title": provider.popular_title,
                    "icon": provider.icon,
                    "hits": hits,
                }
            )
    return blocks


def popular_entries(
    kind: str,
    *,
    scope: Mapping[str, str] | None = None,
    limit: int | None = None,
) -> list[SearchHit]:
    """Popular entries of ``kind`` (cached); empty when disabled or unsupported."""
    settings = popular_settings()
    provider = get_provider(kind)
    if not settings.enabled or provider is None:
        return []
    size = max(1, min(limit or settings.limit, 24))
    clean = _clean_scope(scope)
    version = (cache.get(SETTINGS_VERSION_KEY, 1), popular_version(kind))
    scope_key = ",".join(f"{key}={value}" for key, value in sorted(clean.items()))
    key = (
        f"search:popular:{kind}:{version[0]}.{version[1]}:"
        f"{settings.window_days}:{size}:{scope_key}"
    )
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        hits = list(
            provider.popular(limit=size, window_days=settings.window_days, scope=clean)
        )
    except DatabaseError:
        logger.exception("Popular entries of %r failed", kind)
        return []
    cache.set(key, hits, settings.cache_minutes * 60)
    return hits
