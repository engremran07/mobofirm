"""Database helpers for providers: every query word must match some field."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

from .types import SearchContext

CANDIDATE_FACTOR = 6
MIN_CANDIDATES = 40


def tokens_match(context: SearchContext, fields: Iterable[str]) -> Q:
    """``AND`` over the query words of ``OR`` over ``<field>__icontains``.

    Folded (accent-free) tokens are matched against the raw column, so accented
    stored values only match when the query carries the same accents; the Python
    scoring step still ranks accent-insensitively for what the database returns.
    """
    names = tuple(fields)
    condition = Q()
    for token in context.tokens:
        word = Q()
        for name in names:
            word |= Q(**{f"{name}__icontains": token})
        condition &= word
    return condition


def candidate_limit(context: SearchContext) -> int:
    """How many rows to fetch before scoring and trimming to ``context.limit``."""
    return max(context.limit * CANDIDATE_FACTOR, MIN_CANDIDATES)
