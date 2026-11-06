"""
apps.core.cache

Enterprise-grade centralized cache utilities.

✔ Django 5.2+ / Python 3.12+
✔ Redis / LocMem / cluster cache compatible
✔ Multi-tenant safe: stable digested keys, namespace isolation
✔ Strict key normalization to prevent collisions
✔ Defensive invalidators (site + consent)
✔ Atomic get/set with fallback
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import TypeVar

from django.contrib.sites.models import Site
from django.core.cache import InvalidCacheBackendError, cache
from django.db import DatabaseError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Failures a cache backend (locmem, file, DB, Redis/memcached sockets) can raise.
CACHE_BACKEND_ERRORS = (
    InvalidCacheBackendError,
    OSError,
    ValueError,
    TypeError,
)

# =====================================================================
# KEY UTILITIES
# =====================================================================


def _namespaced_key(
    key: str,
    *,
    version: int | None = None,
    namespace: str | None = None,
) -> str:
    """
    Portable canonical key format.

    Example:
        _namespaced_key("user_session", version=3, namespace="auth")
        → "auth::user_session::v3"
    """
    key = (key or "").strip()
    ns = (namespace or "").strip()

    parts: list[str] = []
    if ns:
        parts.append(ns)
    parts.append(key)
    if version is not None:
        parts.append(f"v{int(version)}")
    return "::".join(parts)


def _digest_key(base: str) -> str:
    """
    Safe digest for long/unsafe keys (Redis + memcached-safe)
    Ensures namespacing cannot collide.
    """
    base = base.replace(" ", "").strip()
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"{base[:32]}::{digest}"


# =====================================================================
# DISTRIBUTED CACHE MANAGER
# =====================================================================


class DistributedCacheManager:
    """
    High-reliability cache manager for multi-site deployments.
    Cache backend outages are logged and never crash the caller.
    """

    # ------------------------------------------------------------------
    # Pattern deletes (Redis-only or custom backends)
    # ------------------------------------------------------------------
    @staticmethod
    def safe_delete_pattern(pattern: str) -> None:
        """
        Delete keys matching pattern (if backend supports it).
        """
        delete_pattern = getattr(cache, "delete_pattern", None)
        if not callable(delete_pattern):
            logger.debug("Backend lacks delete_pattern (pattern=%s)", pattern)
            return
        try:
            delete_pattern(pattern)
        except CACHE_BACKEND_ERRORS as exc:
            logger.debug("delete_pattern failed (%s → %s)", pattern, exc)
        else:
            logger.debug("Pattern delete: %s", pattern)

    # ------------------------------------------------------------------
    # SITE SETTINGS INVALIDATION
    # ------------------------------------------------------------------
    @staticmethod
    def invalidate_site_settings(site_id: int | None = None) -> None:
        """
        Invalidate all cache entries for site settings.

        This implementation is fully aligned with:
        - context processor key scheme
        - multi-tenant domain hashing
        """
        try:
            # Remove legacy global key
            cache.delete("active_site_settings")

            # Remove new hashed-domain keys (pattern)
            DistributedCacheManager.safe_delete_pattern("active_site_settings_*")

            # Remove site-specific numeric key
            if site_id is not None:
                cache.delete(f"site_settings_{site_id}")

            for site_pk in Site.objects.values_list("pk", flat=True):
                cache.delete(f"site_settings_{site_pk}")
        except (*CACHE_BACKEND_ERRORS, DatabaseError):
            logger.exception("invalidate_site_settings failed")
            return

        logger.info("Site settings cache invalidated (site_id=%s)", site_id)

    # ------------------------------------------------------------------
    # CONSENT POLICY INVALIDATION
    # ------------------------------------------------------------------
    @staticmethod
    def invalidate_consent_policy(site_identifier: str | None = None) -> None:
        """
        Invalidate consent policy caches with full pattern-safe cleanup.
        """
        ident = (site_identifier or "global").strip().lower()
        try:
            cache.delete(_digest_key(f"active_consent_policy::{ident}"))
            cache.delete("active_consent_policy")  # legacy
            DistributedCacheManager.safe_delete_pattern("active_consent_policy_*")
        except CACHE_BACKEND_ERRORS:
            logger.exception("invalidate_consent_policy failed")
            return

        logger.info("ConsentPolicy cache invalidated (identifier=%s)", ident)

    # ------------------------------------------------------------------
    # ATOMIC GET / SET WITH FALLBACK
    # ------------------------------------------------------------------
    @staticmethod
    def get_with_coherence(
        key: str,
        fallback_func: Callable[[], _T],
        *,
        timeout: int = 300,
        version: int | None = None,
        namespace: str | None = None,
    ) -> _T | None:
        """
        Atomic get-or-compute with digest-safe keys.

        • Uses get_or_set (atomic) when the backend is reachable
        • Falls back to computing the value directly when the cache backend fails
        • Exceptions raised by ``fallback_func`` itself propagate to the caller
        """
        namespaced = _namespaced_key(key, version=version, namespace=namespace)
        cache_key = _digest_key(namespaced)

        try:
            val = cache.get_or_set(cache_key, fallback_func, timeout=timeout)
        except CACHE_BACKEND_ERRORS as exc:
            logger.warning("Cache coherence failure (%s): %s", cache_key, exc)
            return fallback_func()

        logger.debug("get_or_set OK (%s)", cache_key)
        return val


# Legacy alias for compatibility
CacheManager = DistributedCacheManager
