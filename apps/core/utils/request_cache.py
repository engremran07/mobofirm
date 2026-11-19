"""Per-request memoisation for lookups that are read many times in one request.

``AppRegistry`` alone was fetched up to seven times while rendering one page. A value
memoised here lives exactly as long as the request that computed it:

* ``CorrelationIdMiddleware`` opens a scope around every request and closes it in a
  ``finally`` block, so nothing survives into the next request, a Celery task or a
  test that does not go through the middleware (outside a scope every call computes
  afresh, which keeps management commands, workers and unit tests exact).
* A writer that changes the underlying row calls :func:`forget` (the core signal
  receivers do this for ``AppRegistry`` and ``SearchSettings``), so a request never
  reads its own stale write.

The memo is a ``contextvars`` value, so it is isolated per thread and per asyncio task.
Never memoise per-user data under a shared key: include the user id in the key.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_scope: ContextVar[dict[str, Any] | None] = ContextVar(
    "core_request_cache_scope", default=None
)


@contextmanager
def request_scope() -> Iterator[dict[str, Any]]:
    """Open a fresh memo for the duration of one request (nested scopes are reused)."""
    existing = _scope.get()
    if existing is not None:
        yield existing
        return
    memo: dict[str, Any] = {}
    token = _scope.set(memo)
    try:
        yield memo
    finally:
        _scope.reset(token)


def in_request_scope() -> bool:
    """Whether results are currently being memoised."""
    return _scope.get() is not None


def memoize[T](key: str, compute: Callable[[], T]) -> T:
    """Return the memoised value for ``key``, computing it once per request scope."""
    memo = _scope.get()
    if memo is None:
        return compute()
    if key not in memo:
        memo[key] = compute()
    value: T = memo[key]
    return value


def forget(key: str) -> None:
    """Drop one memoised value (the row behind it changed)."""
    memo = _scope.get()
    if memo is not None:
        memo.pop(key, None)


__all__ = ["forget", "in_request_scope", "memoize", "request_scope"]
