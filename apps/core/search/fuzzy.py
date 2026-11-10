"""Typo tolerance that works on any database (pure Python).

Used when a strict search finds little or nothing. On PostgreSQL with the
``pg_trgm`` extension ``trigram.py`` narrows the candidates inside the database;
everything else, including the SQLite test database, uses these helpers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from .text import fold

_WORD = re.compile(r"[a-z0-9]+")
_HAS_DIGIT = re.compile(r"\d")

MIN_FUZZY_LENGTH: Final[int] = 4
LONG_WORD_LENGTH: Final[int] = 7
MAX_TYPOS: Final[int] = 2


def words(value: str) -> tuple[str, ...]:
    """Folded alphanumeric words of ``value``."""
    return tuple(_WORD.findall(fold(value)))


def allowed_typos(token: str) -> int:
    """Edits tolerated for ``token``.

    Short words and anything containing digits must match exactly: "s23" and
    "s24", or "9200" and "9300", are different devices, not typos.
    """
    if len(token) < MIN_FUZZY_LENGTH or _HAS_DIGIT.search(token):
        return 0
    return 1 if len(token) < LONG_WORD_LENGTH else MAX_TYPOS


def edit_distance(left: str, right: str, limit: int) -> int:
    """Optimal string alignment distance, or ``limit + 1`` once it exceeds ``limit``.

    Adjacent transpositions ("galaxy" / "galxay") count as one edit.
    """
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous2: list[int] = []
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + cost,
            )
            if (
                row > 1
                and column > 1
                and left_char == right[column - 2]
                and left[row - 2] == right_char
            ):
                value = min(value, previous2[column - 2] + 1)
            current.append(value)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    """0..1 closeness of two folded strings (1 when equal)."""
    longest = max(len(left), len(right))
    if longest == 0:
        return 1.0
    distance = edit_distance(left, right, longest)
    return max(0.0, 1.0 - distance / longest)


@dataclass(frozen=True, slots=True)
class FuzzyMatch:
    """A row that matches every query word, at least one of them with a typo."""

    score: float
    replacements: dict[str, str] = field(default_factory=dict)


def _typo_score(token: str, word: str, typos: int) -> float:
    """Similarity of ``word`` to ``token`` within ``typos`` edits, else 0."""
    distance = edit_distance(token, word, typos)
    if distance <= typos:
        return 1.0 - distance / max(len(token), len(word))
    if len(word) > len(token):
        # Type-ahead: the typo may sit in the part typed so far.
        head = edit_distance(token, word[: len(token)], typos)
        if head <= typos:
            return 0.9 - head / len(token)
    return 0.0


def _token_match(token: str, candidates: Iterable[str]) -> tuple[float, str] | None:
    """Best (similarity, word) for ``token``; ``None`` when nothing is close."""
    typos = allowed_typos(token)
    best_score = 0.0
    best_word = ""
    for word in candidates:
        if word.startswith(token) or token in word:
            return 1.0, token
        if not typos:
            continue
        score = _typo_score(token, word, typos)
        if score > best_score:
            best_score, best_word = score, word
    return (best_score, best_word) if best_word else None


def fuzzy_match(tokens: Sequence[str], texts: Iterable[str]) -> FuzzyMatch | None:
    """Match every token against the words of ``texts`` allowing small typos.

    Returns ``None`` unless all tokens match and at least one needed a
    correction (strict matches are the database's job).
    """
    candidates = tuple(dict.fromkeys(word for text in texts for word in words(text)))
    if not tokens or not candidates:
        return None
    total = 0.0
    replacements: dict[str, str] = {}
    for token in tokens:
        matched = _token_match(token, candidates)
        if matched is None:
            return None
        similarity_score, word = matched
        total += similarity_score
        if word != token:
            replacements[token] = word
    if not replacements:
        return None
    return FuzzyMatch(score=total / len(tokens), replacements=replacements)


def corrected_query(query: str, replacements: dict[str, str]) -> str:
    """``query`` with every corrected word swapped for the word that matched."""
    if not replacements:
        return query
    return " ".join(replacements.get(token, token) for token in fold(query).split())
