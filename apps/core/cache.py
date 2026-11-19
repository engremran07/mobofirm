"""
apps.core.cache

Failures a cache backend (locmem, file, DB, Redis/memcached sockets) can raise.
Callers that must keep working when the cache is down catch exactly these.
"""

from __future__ import annotations

from django.core.cache import InvalidCacheBackendError

CACHE_BACKEND_ERRORS = (
    InvalidCacheBackendError,
    OSError,
    ValueError,
    TypeError,
)

__all__ = ["CACHE_BACKEND_ERRORS"]
