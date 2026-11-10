"""Provider registry. Apps contribute providers from ``<app>/search_providers.py``."""

from __future__ import annotations

import logging
from threading import RLock

from .types import SearchProvider
from .vocabulary import connect_invalidation

logger = logging.getLogger(__name__)

_lock = RLock()
_providers: dict[str, SearchProvider] = {}


def register(provider: SearchProvider) -> SearchProvider:
    """Register ``provider`` for its ``kind``.

    Registering the same provider class again is a no-op (module reloads, repeated
    autodiscovery); a different class for an existing kind is a configuration error.
    """
    kind = provider.kind
    with _lock:
        existing = _providers.get(kind)
        if existing is not None and type(existing) is not type(provider):
            raise ValueError(
                f"Search kind {kind!r} is already provided by "
                f"{type(existing).__name__}; cannot register {type(provider).__name__}."
            )
        _providers[kind] = provider
    connect_invalidation(provider)
    return provider


def unregister(kind: str) -> None:
    """Remove the provider of ``kind`` (tests, optional apps)."""
    with _lock:
        _providers.pop(kind, None)


def get_provider(kind: str) -> SearchProvider | None:
    """The registered provider of ``kind``, if any."""
    with _lock:
        return _providers.get(kind)


def get_providers(
    user: object | None = None, *, kinds: tuple[str, ...] | None = None
) -> list[SearchProvider]:
    """Providers visible to ``user``, ordered by priority.

    Without ``kinds`` only navigable providers are returned; explicit ``kinds`` may
    name pick-only providers too. Staff-only providers are never returned to others.
    """
    with _lock:
        candidates = list(_providers.values())
    if kinds is not None:
        wanted = set(kinds)
        candidates = [provider for provider in candidates if provider.kind in wanted]
    else:
        candidates = [provider for provider in candidates if provider.navigable]
    visible = [provider for provider in candidates if provider.is_visible_to(user)]
    return sorted(visible, key=lambda provider: (provider.priority, provider.kind))
