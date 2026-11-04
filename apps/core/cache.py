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
from django.core.cache import cache

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

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
    All operations are fully defensive (never crash).
    """

    # ------------------------------------------------------------------
    # Pattern deletes (Redis-only or custom backends)
    # ------------------------------------------------------------------
    @staticmethod
    def safe_delete_pattern(pattern: str) -> None:
        """
        Delete keys matching pattern (if backend supports it).
        """
        try:
            if hasattr(cache, "delete_pattern"):
                cache.delete_pattern(pattern)  # type: ignore[attr-defined]
                logger.debug("Pattern delete: %s", pattern)
            else:
                logger.debug("Backend lacks delete_pattern (pattern=%s)", pattern)
        except Exception as exc:
            logger.debug("delete_pattern failed (%s → %s)", pattern, exc)

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
            try:
                cache.delete("active_site_settings")
            except Exception:  # noqa: S110
                pass

            # Remove new hashed-domain keys (pattern)
            DistributedCacheManager.safe_delete_pattern("active_site_settings_*")

            # Remove site-specific numeric key
            if site_id is not None:
                try:
                    cache.delete(f"site_settings_{site_id}")
                except Exception:  # noqa: S110
                    pass

            # Defensive enumeration (non-fatal)
            try:
                for s in Site.objects.only("id"):
                    try:
                        cache.delete(f"site_settings_{s.pk}")
                    except Exception:  # noqa: S110
                        pass
            except Exception:  # noqa: S110
                pass

            logger.info("Site settings cache invalidated (site_id=%s)", site_id)

        except Exception as exc:
            logger.exception("invalidate_site_settings failed → %s", exc)

    # ------------------------------------------------------------------
    # CONSENT POLICY INVALIDATION
    # ------------------------------------------------------------------
    @staticmethod
    def invalidate_consent_policy(site_identifier: str | None = None) -> None:
        """
        Invalidate consent policy caches with full pattern-safe cleanup.
        """
        try:
            ident = (site_identifier or "global").strip().lower()
            digest_key = _digest_key(f"active_consent_policy::{ident}")

            # exact delete
            try:
                cache.delete(digest_key)
            except Exception:  # noqa: S110
                pass

            # legacy
            try:
                cache.delete("active_consent_policy")
            except Exception:  # noqa: S110
                pass

            # pattern cleanup
            DistributedCacheManager.safe_delete_pattern("active_consent_policy_*")

            logger.info("ConsentPolicy cache invalidated (identifier=%s)", ident)

        except Exception as exc:
            logger.exception("invalidate_consent_policy failed → %s", exc)

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

        • Uses get_or_set when available (atomic)
        • Falls back to manual get → compute → set
        • Never raises exceptions
        """
        try:
            namespaced = _namespaced_key(key, version=version, namespace=namespace)
            cache_key = _digest_key(namespaced)

            # Preferred atomic path
            if hasattr(cache, "get_or_set"):
                try:
                    val = cache.get_or_set(cache_key, fallback_func, timeout=timeout)
                    logger.debug("get_or_set OK (%s)", cache_key)
                    return val
                except Exception:
                    logger.debug("get_or_set failed → manual fallback")

            # Manual path
            try:
                existing = cache.get(cache_key)
                if existing is not None:
                    logger.debug("Cache HIT (%s)", cache_key)
                    return existing
            except Exception:
                logger.debug("Cache.get failed for %s", cache_key)

            logger.debug("Cache MISS (%s) — computing fallback", cache_key)
            val = fallback_func()

            # Race-safe: try add() before set()
            try:
                added = False
                if hasattr(cache, "add"):
                    added = cache.add(cache_key, val, timeout=timeout)
                if not added:
                    cache.set(cache_key, val, timeout=timeout)
                logger.debug("Stored (%s, added=%s)", cache_key, added)
            except Exception:
                logger.debug("Cache set/add failed (%s)", cache_key)

            return val

        except Exception as exc:
            logger.warning("Cache coherence failure (%s)", exc)
            try:
                return fallback_func()
            except Exception as inner:
                logger.exception("Fallback compute failed → %s", inner)
                return None


# Legacy alias for compatibility
CacheManager = DistributedCacheManager
