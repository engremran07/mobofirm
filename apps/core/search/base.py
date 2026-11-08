"""Base class for providers backed by one queryset."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import ClassVar

from django.db.models import Model, QuerySet

from .db import candidate_limit, tokens_match
from .text import score_text
from .types import SearchContext, SearchHit, SearchProvider


class QuerySetProvider[T: Model](SearchProvider):
    """Fetch candidate rows that contain every query word, then rank them.

    Subclasses define the visible rows (``get_queryset``: permissions and public
    state live there), which columns to match (``search_fields``), which strings
    drive the relevance score (``score_fields``, most important first) and how a
    row becomes a hit (``build_hit``).
    """

    search_fields: ClassVar[tuple[str, ...]]
    ordering: ClassVar[tuple[str, ...]] = ("pk",)

    @abstractmethod
    def get_queryset(self, context: SearchContext) -> QuerySet[T]:
        """Rows the requesting user may see."""

    @abstractmethod
    def score_fields(self, obj: T) -> tuple[str, ...]:
        """Strings compared with the query, most important first."""

    @abstractmethod
    def build_hit(self, obj: T, score: float, context: SearchContext) -> SearchHit:
        """Turn a row into a hit."""

    def search(self, context: SearchContext) -> Iterable[SearchHit]:
        queryset = self.get_queryset(context)
        if context.tokens:
            queryset = queryset.filter(tokens_match(context, self.search_fields))
        rows = queryset.order_by(*self.ordering)[: candidate_limit(context)]
        hits: list[SearchHit] = []
        for obj in rows:
            if context.tokens:
                score = score_text(
                    context.query, context.tokens, *self.score_fields(obj)
                )
                if score <= 0:
                    continue
            else:
                score = 0.0
            hits.append(self.build_hit(obj, score, context))
        return hits
