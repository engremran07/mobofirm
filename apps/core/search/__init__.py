"""Central search engine.

Apps contribute ``SearchProvider`` instances from ``<app>/search_providers.py``
(imported by ``CoreConfig.ready``); ``search()`` merges their hits. The
HTTP surface is ``search/`` (results page) and ``search/suggest/`` (HTMX partial).
"""

from __future__ import annotations

from .base import QuerySetProvider
from .engine import DEFAULT_LIMIT, MAX_LIMIT, HitGroup, search, search_grouped
from .registry import get_provider, get_providers, register, unregister
from .types import SearchContext, SearchHit, SearchProvider

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "HitGroup",
    "QuerySetProvider",
    "SearchContext",
    "SearchHit",
    "SearchProvider",
    "get_provider",
    "get_providers",
    "register",
    "search",
    "search_grouped",
    "unregister",
]
