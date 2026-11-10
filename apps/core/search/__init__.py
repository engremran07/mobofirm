"""Central search engine.

Apps contribute ``SearchProvider`` instances from ``<app>/search_providers.py``
(imported by ``CoreConfig.ready``); ``search()`` merges their hits. The
HTTP surface is ``search/`` (results page) and ``search/suggest/`` (HTMX partial).
"""

from __future__ import annotations

from .base import QuerySetProvider
from .engine import DEFAULT_LIMIT, MAX_LIMIT, HitGroup, search, search_grouped
from .listing import (
    ItemSearch,
    ListSearch,
    MultiSearch,
    list_search_context,
    search_items,
    search_queryset,
    search_querysets,
)
from .popular import PopularSettings, popular_entries, popular_settings
from .registry import get_provider, get_providers, register, unregister
from .suggest import FEW_RESULTS, Suggestion, did_you_mean
from .types import SearchContext, SearchHit, SearchProvider, VocabTerm

__all__ = [
    "DEFAULT_LIMIT",
    "FEW_RESULTS",
    "MAX_LIMIT",
    "HitGroup",
    "ItemSearch",
    "ListSearch",
    "MultiSearch",
    "PopularSettings",
    "QuerySetProvider",
    "SearchContext",
    "SearchHit",
    "SearchProvider",
    "Suggestion",
    "VocabTerm",
    "did_you_mean",
    "get_provider",
    "get_providers",
    "list_search_context",
    "popular_entries",
    "popular_settings",
    "register",
    "search",
    "search_grouped",
    "search_items",
    "search_queryset",
    "search_querysets",
    "unregister",
]
