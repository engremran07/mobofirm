"""One search for every list page: same normalisation, synonyms and typo handling.

List views (chipsets, brands, tags, tools, guides, ...) call ``search_queryset``
instead of writing their own ``icontains`` filters::

    result = search_queryset(Chipset.objects.all(), query, CHIPSET_FIELDS)
    page = Paginator(result.queryset, 48).get_page(number)
    context.update(list_search_context(result, page.paginator.count, user=user))

A strict pass requires every query word (or a synonym) to occur in some field. When
that finds nothing the typo-tolerant pass ("snapdargon") takes over, using
``pg_trgm`` on PostgreSQL to shortlist rows and pure Python to confirm them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from django.db.models import Expression, QuerySet

from .db import token_condition
from .fuzzy import FuzzyMatch, corrected_query, fuzzy_match
from .suggest import FEW_RESULTS, Suggestion, did_you_mean
from .text import clean_query, score_text, tokenize
from .trigram import trigram_available, trigram_candidates

FUZZY_ROW_CAP: Final[int] = 5000


@dataclass(frozen=True, slots=True)
class ListSearch:
    """Outcome of ``search_queryset``.

    ``corrected`` is the spelling the typo-tolerant pass matched (empty when the
    strict pass answered); ``query`` is always what the visitor typed.
    """

    queryset: QuerySet[Any]
    query: str
    corrected: str = ""

    @property
    def is_active(self) -> bool:
        """``True`` when a query narrowed the list."""
        return bool(self.query)


def fuzzy_matches(
    queryset: QuerySet[Any],
    fields: Iterable[str],
    tokens: tuple[str, ...],
) -> dict[Any, FuzzyMatch]:
    """Rows of ``queryset`` that match every word once typos are forgiven."""
    names = tuple(fields)
    source = queryset
    if trigram_available():
        source = trigram_candidates(queryset, names, tokens)
    rows = (
        source.order_by()
        .prefetch_related(None)
        .values_list("pk", *names)[:FUZZY_ROW_CAP]
    )
    matches: dict[Any, FuzzyMatch] = {}
    for pk, *values in rows:
        found = fuzzy_match(tokens, (str(value) for value in values if value))
        if found is not None:
            matches[pk] = found
    return matches


@dataclass(frozen=True, slots=True)
class ItemSearch[T]:
    """Outcome of ``search_items``: the matching items in their original order."""

    items: list[T]
    query: str
    corrected: str = ""

    @property
    def is_active(self) -> bool:
        """``True`` when a query narrowed the list."""
        return bool(self.query)


@dataclass(frozen=True, slots=True)
class MultiSearch:
    """Outcome of ``search_querysets``: one filtered queryset per key."""

    querysets: dict[str, QuerySet[Any]]
    query: str
    corrected: str = ""

    @property
    def is_active(self) -> bool:
        """``True`` when a query narrowed the lists."""
        return bool(self.query)


def search_querysets(
    querysets: Mapping[str, QuerySet[Any]],
    query: str | None,
    fields: Iterable[str],
) -> MultiSearch:
    """``search_queryset`` over several tables that are listed together.

    The typo-tolerant pass runs only when *no* table has a strict match, so one
    table's near misses never hide another table's exact matches.
    """
    cleaned = clean_query(query)
    tokens = tokenize(cleaned)
    if not tokens:
        return MultiSearch(dict(querysets), cleaned)
    names = tuple(fields)
    condition = token_condition(tokens, names)
    strict = {key: queryset.filter(condition) for key, queryset in querysets.items()}
    if any(queryset.exists() for queryset in strict.values()):
        return MultiSearch(strict, cleaned)
    matches = {
        key: fuzzy_matches(queryset, names, tokens)
        for key, queryset in querysets.items()
    }
    found = [match for rows in matches.values() for match in rows.values()]
    if not found:
        return MultiSearch({k: q.none() for k, q in querysets.items()}, cleaned)
    best = max(found, key=lambda match: match.score)
    return MultiSearch(
        {
            key: queryset.filter(pk__in=list(matches[key]))
            for key, queryset in querysets.items()
        },
        cleaned,
        corrected_query(cleaned, best.replacements),
    )


def search_queryset(
    queryset: QuerySet[Any],
    query: str | None,
    fields: Iterable[str],
    *,
    annotations: Mapping[str, Expression] | None = None,
) -> ListSearch:
    """Filter ``queryset`` by ``query`` over ``fields`` (``icontains`` paths).

    ``annotations`` adds computed text columns first (for example a JSON column
    cast to text) so they can be listed in ``fields``.
    """
    base = queryset.annotate(**annotations) if annotations else queryset
    outcome = search_querysets({"rows": base}, query, fields)
    return ListSearch(outcome.querysets["rows"], outcome.query, outcome.corrected)


def search_items[T](
    items: Sequence[T],
    query: str | None,
    texts: Callable[[T], Sequence[str]],
) -> ItemSearch[T]:
    """Filter an in-memory list (rows assembled in Python) with the same rules.

    ``texts`` returns the strings of one item that the query may match. Strict
    matching uses the shared ranking function (words, accents, synonyms); the
    typo-tolerant pass runs only when nothing matched strictly.
    """
    cleaned = clean_query(query)
    tokens = tokenize(cleaned)
    if not tokens:
        return ItemSearch(list(items), cleaned)
    strict = [item for item in items if score_text(cleaned, tokens, *texts(item)) > 0]
    if strict:
        return ItemSearch(strict, cleaned)
    found: list[tuple[T, FuzzyMatch]] = []
    for item in items:
        match = fuzzy_match(tokens, texts(item))
        if match is not None:
            found.append((item, match))
    if not found:
        return ItemSearch([], cleaned)
    best = max(match.score for _item, match in found)
    top = next(match for _item, match in found if match.score == best)
    return ItemSearch(
        [item for item, _match in found],
        cleaned,
        corrected_query(cleaned, top.replacements),
    )


def list_search_context(
    result: ListSearch | MultiSearch | ItemSearch[Any],
    total: int,
    *,
    user: object | None = None,
    kinds: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Template context for the shared live-search and "did you mean" components."""
    suggestions: tuple[Suggestion, ...] = ()
    if result.is_active and total < FEW_RESULTS:
        suggestions = tuple(did_you_mean(result.query, user=user, kinds=kinds))
    return {
        "search_query": result.query,
        "search_corrected": result.corrected,
        "search_total": total,
        "did_you_mean": suggestions,
    }
