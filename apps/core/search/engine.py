"""``search()``: ask the visible providers and merge their hits by relevance."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Final

from django.db import DatabaseError

from .registry import get_providers
from .suggest import FEW_RESULTS
from .text import clean_query, tokenize
from .types import SearchContext, SearchHit, SearchProvider

logger = logging.getLogger(__name__)

DEFAULT_LIMIT: Final[int] = 10
MAX_LIMIT: Final[int] = 50


@dataclass(frozen=True, slots=True)
class HitGroup:
    """Hits of one kind, in rank order, for the grouped results page."""

    kind: str
    label: str
    label_plural: str
    icon: str
    hits: tuple[SearchHit, ...]


def _clamp(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _wants_query(provider: SearchProvider, query: str, *, picker: bool) -> bool:
    if len(query) >= provider.min_length:
        return True
    return picker and not query and provider.supports_browse


def _provider_hits(provider: SearchProvider, context: SearchContext) -> list[SearchHit]:
    """Ask one provider; a failing provider must not take the whole search down."""
    try:
        return list(provider.search(context))
    except DatabaseError:
        logger.exception("Search provider %r failed", provider.kind)
        return []


def _plan(
    query: str | None,
    kinds: tuple[str, ...] | list[str] | None,
    limit: int,
    user: object | None,
    filters: dict[str, str] | None,
) -> tuple[list[SearchProvider], SearchContext]:
    cleaned = clean_query(query)
    providers = [
        provider
        for provider in get_providers(user, kinds=tuple(kinds) if kinds else None)
        if _wants_query(provider, cleaned, picker=bool(kinds))
    ]
    context = SearchContext(
        query=cleaned,
        tokens=tokenize(cleaned),
        limit=_clamp(limit),
        user=user,
        filters=dict(filters or {}),
    )
    return providers, context


def _gather(
    providers: list[SearchProvider], context: SearchContext
) -> dict[str, list[SearchHit]]:
    """Hits per provider kind; a typo-tolerant second pass runs when few were found.

    Only providers that found nothing are asked again, so strict matches are never
    diluted by near misses.
    """
    found = {provider.kind: _provider_hits(provider, context) for provider in providers}
    if not context.tokens or sum(len(hits) for hits in found.values()) >= FEW_RESULTS:
        return found
    relaxed = replace(context, fuzzy=True)
    for provider in providers:
        if not found[provider.kind]:
            found[provider.kind] = _provider_hits(provider, relaxed)
    return found


def search(
    query: str | None,
    *,
    kinds: tuple[str, ...] | list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    user: object | None = None,
    filters: dict[str, str] | None = None,
) -> list[SearchHit]:
    """Ranked hits across the providers visible to ``user``.

    ``kinds`` restricts the search (and may name pick-only kinds). Queries shorter
    than a provider's ``min_length`` skip that provider; an empty query returns a
    browse list only when ``kinds`` names providers that support it (pickers).
    Sorting is by score, then provider priority, then title, so results are
    deterministic. When fewer than ``FEW_RESULTS`` hits are found, providers that
    found nothing are asked again with typo tolerance.
    """
    providers, context = _plan(query, kinds, limit, user, filters)
    priority = {provider.kind: provider.priority for provider in providers}
    hits: list[SearchHit] = []
    for found in _gather(providers, context).values():
        hits.extend(found)
    hits.sort(key=lambda hit: (-hit.score, priority.get(hit.kind, 999), hit.title))
    return hits[: context.limit]


def search_grouped(
    query: str | None,
    *,
    kinds: tuple[str, ...] | list[str] | None = None,
    per_kind: int = DEFAULT_LIMIT,
    user: object | None = None,
    filters: dict[str, str] | None = None,
) -> list[HitGroup]:
    """Results of ``search`` split into one group per kind (best group first)."""
    providers, context = _plan(query, kinds, per_kind, user, filters)
    ranked: dict[str, tuple[SearchProvider, list[SearchHit]]] = {}
    gathered = _gather(providers, context)
    for provider in providers:
        hits = sorted(
            gathered[provider.kind],
            key=lambda hit: (-hit.score, hit.title),
        )
        if hits:
            ranked[provider.kind] = (provider, hits[: context.limit])
    ordered = sorted(
        ranked.values(),
        key=lambda pair: (-pair[1][0].score, pair[0].priority, pair[0].kind),
    )
    return [
        HitGroup(
            kind=provider.kind,
            label=provider.label,
            label_plural=provider.label_plural,
            icon=provider.icon,
            hits=tuple(hits),
        )
        for provider, hits in ordered
    ]
