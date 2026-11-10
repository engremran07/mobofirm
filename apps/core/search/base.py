"""Base class for providers backed by one queryset."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Iterable
from typing import ClassVar, Final

from django.db.models import Model, QuerySet

from .db import candidate_limit, tokens_match
from .listing import fuzzy_matches
from .text import score_text
from .types import SearchContext, SearchHit, SearchProvider, VocabTerm

# A typo-tolerant hit ranks below every strict hit (strict scores start at 40).
FUZZY_SCORE_BASE: Final[float] = 20.0
FUZZY_SCORE_SPAN: Final[float] = 15.0


class QuerySetProvider[T: Model](SearchProvider):
    """Fetch candidate rows that contain every query word, then rank them.

    Subclasses define the visible rows (``get_queryset``: permissions and public
    state live there), which columns to match (``search_fields``), which strings
    drive the relevance score (``score_fields``, most important first) and how a
    row becomes a hit (``build_hit``).
    """

    search_fields: ClassVar[tuple[str, ...]]
    ordering: ClassVar[tuple[str, ...]] = ("pk",)
    # Offer this kind in "did you mean" suggestions (names must be short).
    suggestible: ClassVar[bool] = False
    # Models whose saves and deletes invalidate the cached suggestion vocabulary.
    watches: ClassVar[tuple[type[Model], ...]] = ()
    vocabulary_size: ClassVar[int] = 3000

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
        if context.fuzzy and context.tokens:
            return self._fuzzy_hits(queryset, context)
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

    def _fuzzy_hits(
        self, queryset: QuerySet[T], context: SearchContext
    ) -> list[SearchHit]:
        matches = fuzzy_matches(queryset, self.search_fields, context.tokens)
        if not matches:
            return []
        rows = queryset.filter(pk__in=list(matches)).order_by(*self.ordering)[
            : candidate_limit(context)
        ]
        return [
            self.build_hit(
                obj,
                FUZZY_SCORE_BASE + FUZZY_SCORE_SPAN * matches[obj.pk].score,
                context,
            )
            for obj in rows
        ]

    def watched_models(self) -> Iterable[type]:
        return self.watches

    def vocabulary(self) -> Iterable[VocabTerm]:
        if not self.suggestible:
            return ()
        context = SearchContext(query="", tokens=(), limit=self.vocabulary_size)
        rows = self.get_queryset(context).order_by(*self.ordering)
        terms: list[VocabTerm] = []
        for obj in rows[: self.vocabulary_size]:
            hit = self.build_hit(obj, 0.0, context)
            if not hit.url:
                continue
            terms.append(
                VocabTerm(
                    kind=self.kind,
                    title=hit.title,
                    url=hit.url,
                    icon=hit.icon,
                    terms=(hit.title, *self.score_fields(obj)),
                )
            )
        return terms
