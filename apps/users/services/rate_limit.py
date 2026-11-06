"""
apps.users.services.rate_limit
------------------------------
Cache-based rate limiter for authentication and signup actions.

Counters use ``cache.add`` + ``cache.incr`` so the check-and-increment is atomic
on shared backends (Redis). Per-process backends such as ``LocMemCache`` only
limit a single worker; production settings therefore require a shared cache.
"""

import logging

from django.core.cache import cache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

CACHE_ERRORS = (RedisError, OSError, TimeoutError)


def allow_action(
    key: str,
    max_attempts: int = 5,
    window_seconds: int = 300,
    fail_open: bool = True,
) -> bool:
    """
    Count one attempt for ``key`` and report whether it is within the limit.

    The window starts at the first attempt and is fixed; attempts rejected after
    the limit still count so a client cannot probe the boundary for free.

    Args:
        key: Unique cache key, e.g. ``"login:1.2.3.4"``.
        max_attempts: Attempts allowed per window.
        window_seconds: Window length in seconds.
        fail_open: Allow the action when the cache backend fails. Security
            critical callers (login, MFA, password reset) pass ``False``.
    """
    if not key:
        logger.warning("allow_action called with empty key.")
        return False

    try:
        cache.add(key, 0, timeout=window_seconds)
        try:
            attempts = cache.incr(key)
        except (TypeError, ValueError):
            # The entry expired between add() and incr(), or holds a stale value.
            cache.set(key, 1, timeout=window_seconds)
            attempts = 1
    except CACHE_ERRORS:
        logger.exception("Rate limiter backend failure for %s", key)
        return fail_open

    if attempts > max_attempts:
        logger.info(
            "Rate limit exceeded: key=%s, attempts=%d/%d, window=%ds",
            key,
            attempts,
            max_attempts,
            window_seconds,
        )
        return False
    return True


def reset_rate_limit(key: str) -> None:
    """Clear the counter for ``key`` (for example after a successful login)."""
    try:
        cache.delete(key)
    except CACHE_ERRORS:
        logger.warning("Failed to reset rate limit for %s", key, exc_info=True)


def get_attempt_count(
    key: str, window_seconds: int = 300, fail_open: bool = True
) -> int:
    """
    Return the attempts counted in the current window for ``key``.

    ``window_seconds`` is accepted for API compatibility; the window is set when
    the first attempt is counted.
    """
    try:
        value = cache.get(key, 0)
    except CACHE_ERRORS:
        logger.warning("Failed to read attempt count for %s", key, exc_info=True)
        if not fail_open:
            raise
        return 0
    return value if isinstance(value, int) and value > 0 else 0
