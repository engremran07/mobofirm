"""Did you mean ...? The closest brand, model, chipset, tool or firmware type.

Providers publish a small public vocabulary (``SearchProvider.vocabulary``). It is
cached per kind and rebuilt when a watched model changes, so a suggestion costs
one cache read plus a bounded string comparison, never a database scan.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final

from .fuzzy import allowed_typos, edit_distance, similarity
from .registry import get_providers
from .text import clean_query, fold, tokenize
from .types import SearchProvider, VocabTerm
from .vocabulary import provider_vocabulary

# Lists and results with fewer rows than this offer suggestions.
FEW_RESULTS: Final[int] = 3
MAX_SUGGESTIONS: Final[int] = 3
SIMILARITY_FLOOR: Final[float] = 0.8
MAX_TERM_LENGTH: Final[int] = 48
MIN_TOKEN_LENGTH: Final[int] = 4
_DIGIT_TYPOS: Final[int] = 1
_TOKEN_DISCOUNT: Final[float] = 0.95


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A near miss for the query, labelled with the kind of thing it is."""

    kind: str
    noun: str
    title: str
    url: str
    icon: str
    score: float

    @property
    def label(self) -> str:
        """``Samsung (brand)``."""
        return f"{self.title} ({self.noun})"


def _digits(value: str) -> str:
    """Only the digits: numbers in a name are never "typos" of other numbers."""
    return "".join(char for char in value if char.isdigit())


def _near(piece: str, candidate: str) -> float:
    """Similarity of a typo-like near miss; 0 for equal or unrelated strings."""
    if piece == candidate or len(piece) < MIN_TOKEN_LENGTH:
        return 0.0
    closeness = similarity(piece, candidate)
    if closeness < SIMILARITY_FLOOR:
        return 0.0
    has_digit = any(char.isdigit() for char in piece)
    if has_digit and _digits(piece) != _digits(candidate):
        return 0.0
    limit = _DIGIT_TYPOS if has_digit else allowed_typos(piece)
    if edit_distance(piece, candidate, limit) > limit:
        return 0.0
    return closeness


def _term_score(pieces: tuple[tuple[str, float], ...], term: VocabTerm) -> float:
    best = 0.0
    for text in term.terms:
        folded = fold(text)
        if not folded or len(folded) > MAX_TERM_LENGTH:
            continue
        for piece, weight in pieces:
            best = max(best, _near(piece, folded) * weight)
    return best


def _suggestions_for(
    provider: SearchProvider,
    pieces: tuple[tuple[str, float], ...],
    skip: set[str],
) -> Iterator[tuple[tuple[float, int, int, str], Suggestion]]:
    """Ranked suggestions one provider's vocabulary offers for the query pieces."""
    for term in provider_vocabulary(provider):
        score = 0.0 if term.url in skip else _term_score(pieces, term)
        if score <= 0:
            continue
        suggestion = Suggestion(
            kind=provider.kind,
            noun=provider.entity_noun,
            title=term.title,
            url=term.url,
            icon=term.icon,
            score=score,
        )
        yield (-score, provider.priority, len(term.title), term.title), suggestion


def did_you_mean(
    query: str | None,
    *,
    user: object | None = None,
    kinds: tuple[str, ...] | None = None,
    exclude_urls: Iterable[str] = (),
    limit: int = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """Closest named entities to a misspelt ``query``, best first.

    Only near misses are returned: names equal to the query are what a normal
    search already finds. ``exclude_urls`` drops suggestions already shown as hits.
    """
    cleaned = clean_query(query)
    if not cleaned:
        return []
    pieces = [(fold(cleaned), 1.0)]
    tokens = tokenize(cleaned)
    if len(tokens) > 1:
        pieces.extend((token, _TOKEN_DISCOUNT) for token in tokens)
    skip = set(exclude_urls)
    found: dict[tuple[str, str], tuple[tuple[float, int, int, str], Suggestion]] = {}
    for provider in get_providers(user, kinds=kinds):
        for rank, suggestion in _suggestions_for(provider, tuple(pieces), skip):
            key = (suggestion.kind, suggestion.url)
            if key not in found or rank < found[key][0]:
                found[key] = (rank, suggestion)
    ordered = sorted(found.values(), key=lambda pair: pair[0])
    return [suggestion for _rank, suggestion in ordered[: max(1, limit)]]
