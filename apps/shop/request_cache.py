"""Request-scoped memo for resolved entitlements (no model imports, no cycles).

Outside a request (tests, tasks, shell) there is no cache, so every call resolves
fresh. Code that changes what a user is entitled to calls :func:`invalidate` so the
rest of the same request sees the change.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_cache: ContextVar[dict[Any, Any] | None] = ContextVar(
    "shop_entitlements_cache", default=None
)


def begin() -> Token[dict[Any, Any] | None]:
    return _cache.set({})


def end(token: Token[dict[Any, Any] | None]) -> None:
    _cache.reset(token)


def lookup(key: Any) -> Any | None:
    store = _cache.get()
    return None if store is None else store.get(key)


def remember(key: Any, value: Any) -> None:
    store = _cache.get()
    if store is not None:
        store[key] = value


def invalidate() -> None:
    store = _cache.get()
    if store is not None:
        store.clear()
