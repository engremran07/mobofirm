"""Database helpers for providers: every query word must match some field."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

from .synonyms import expand
from .types import SearchContext

CANDIDATE_FACTOR = 6
MIN_CANDIDATES = 40


def token_condition(tokens: Iterable[str], fields: Iterable[str]) -> Q:
    """``AND`` over the words of ``OR`` over ``<field>__icontains``.

    Each word also matches its curated synonyms ("mtk" finds MediaTek). Folded
    (accent-free) tokens are matched against the raw column, so accented stored
    values only match when the query carries the same accents; the Python scoring
    step still ranks accent-insensitively for what the database returns.
    """
    names = tuple(fields)
    condition = Q()
    for token in tokens:
        word = Q()
        for variant in (token, *expand(token)):
            for name in names:
                word |= Q(**{f"{name}__icontains": variant})
        condition &= word
    return condition


def tokens_match(context: SearchContext, fields: Iterable[str]) -> Q:
    """``token_condition`` for the words of a provider's search context."""
    return token_condition(context.tokens, fields)


def candidate_limit(context: SearchContext) -> int:
    """How many rows to fetch before scoring and trimming to ``context.limit``."""
    return max(context.limit * CANDIDATE_FACTOR, MIN_CANDIDATES)
