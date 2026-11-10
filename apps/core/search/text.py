"""Text normalisation and relevance scoring shared by every search provider."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from .synonyms import expand

MAX_QUERY_LENGTH: Final[int] = 100
MAX_TOKENS: Final[int] = 6

_WHITESPACE = re.compile(r"\s+")

# Score bands: exact > prefix > word prefix > substring. Earlier fields carry a
# small bonus so a name hit outranks a description hit.
SCORE_EXACT: Final[float] = 100.0
SCORE_PREFIX: Final[float] = 80.0
SCORE_WORD_PREFIX: Final[float] = 60.0
SCORE_CONTAINS: Final[float] = 40.0
# A synonym match ranks just below the literal word.
SYNONYM_PENALTY: Final[float] = 5.0


def fold(value: str) -> str:
    """Lower-case ``value`` and strip accents so "Côte" matches "cote"."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", stripped.casefold()).strip()


def clean_query(raw: str | None) -> str:
    """Trim, collapse whitespace and cap the length of a user supplied query."""
    return _WHITESPACE.sub(" ", (raw or "").strip())[:MAX_QUERY_LENGTH]


def tokenize(query: str) -> tuple[str, ...]:
    """Folded, de-duplicated query words (at most ``MAX_TOKENS``)."""
    seen: dict[str, None] = {}
    for word in fold(query).split(" "):
        if word:
            seen.setdefault(word)
    return tuple(seen)[:MAX_TOKENS]


def _field_bonus(position: int) -> float:
    return max(0.0, 9.0 - position * 3.0)


def _word_score(word: str, haystack: str, bonus: float) -> float:
    if haystack.startswith(word):
        return SCORE_PREFIX - 10 + bonus
    if f" {word}" in haystack:
        return SCORE_WORD_PREFIX + bonus
    if word in haystack:
        return SCORE_CONTAINS + bonus
    return 0.0


def score_text(query: str, tokens: Iterable[str], *fields: str) -> float:
    """Relevance of ``fields`` for a query; 0 when some token matches no field.

    ``fields`` are ordered by importance: the first field carries the most weight.
    """
    words = tuple(tokens)
    folded_fields = [fold(field) for field in fields if field]
    if not words or not folded_fields:
        return 0.0
    needle = fold(query)
    phrase = 0.0
    for position, haystack in enumerate(folded_fields):
        if haystack == needle:
            phrase = max(phrase, SCORE_EXACT + _field_bonus(position))
        elif haystack.startswith(needle):
            phrase = max(phrase, SCORE_PREFIX + _field_bonus(position))
    total = 0.0
    for word in words:
        word_best = 0.0
        for variant in (word, *expand(word)):
            penalty = 0.0 if variant == word else SYNONYM_PENALTY
            for position, haystack in enumerate(folded_fields):
                word_best = max(
                    word_best,
                    _word_score(variant, haystack, _field_bonus(position)) - penalty,
                )
        if word_best <= 0.0:
            return 0.0
        total += word_best
    return max(phrase, total / len(words))
