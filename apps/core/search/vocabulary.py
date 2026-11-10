"""Cached public vocabulary of the providers (names a suggestion may point at)."""

from __future__ import annotations

import logging
from typing import Final

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save

from .types import SearchProvider, VocabTerm

logger = logging.getLogger(__name__)

VOCABULARY_TTL: Final[int] = 600
VOCABULARY_LIMIT: Final[int] = 3000


def _version_key(kind: str) -> str:
    return f"search:vocabulary-version:{kind}"


def _popular_key(kind: str) -> str:
    return f"search:popular-version:{kind}"


def popular_version(kind: str) -> int:
    """Counter that changes whenever data behind ``kind``'s popular list changed."""
    return int(cache.get(_popular_key(kind), 1))


def _bump(key: str) -> None:
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 2, None)


def invalidate_vocabulary(kind: str) -> None:
    """Drop the cached vocabulary and popular lists of ``kind``."""
    _bump(_version_key(kind))
    _bump(_popular_key(kind))


def provider_vocabulary(provider: SearchProvider) -> tuple[VocabTerm, ...]:
    """The (cached) names ``provider`` can be suggested by."""
    version = cache.get(_version_key(provider.kind), 1)
    key = f"search:vocabulary:{provider.kind}:{version}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        terms = tuple(provider.vocabulary())[:VOCABULARY_LIMIT]
    except DatabaseError:
        logger.exception("Search vocabulary of %r failed", provider.kind)
        return ()
    cache.set(key, terms, VOCABULARY_TTL)
    return terms


def connect_invalidation(provider: SearchProvider) -> None:
    """Rebuild the vocabulary whenever a watched model row changes."""
    kind = provider.kind

    def _invalidate(**_kwargs: object) -> None:
        invalidate_vocabulary(kind)

    for model in provider.watched_models():
        uid = f"search-vocabulary-{kind}-{model.__name__}"
        post_save.connect(_invalidate, sender=model, weak=False, dispatch_uid=uid)
        post_delete.connect(
            _invalidate, sender=model, weak=False, dispatch_uid=f"{uid}-delete"
        )
