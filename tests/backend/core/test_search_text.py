"""Search normalisation, synonyms, ranking and typo helpers (no database)."""

from __future__ import annotations

import pytest

from apps.core.search.fuzzy import (
    allowed_typos,
    corrected_query,
    edit_distance,
    fuzzy_match,
    similarity,
)
from apps.core.search.synonyms import expand
from apps.core.search.text import clean_query, fold, score_text, tokenize


def test_fold_strips_accents_case_and_whitespace() -> None:
    """Accents, case and repeated spaces never change a match."""
    assert fold("  Côte   D'Ivoire ") == "cote d'ivoire"


def test_clean_query_caps_length_and_collapses_space() -> None:
    """Queries are trimmed, collapsed and capped at 100 characters."""
    assert clean_query("  a   b ") == "a b"
    assert len(clean_query("x" * 500)) == 100
    assert clean_query(None) == ""


def test_tokenize_dedupes_and_limits_words() -> None:
    """Repeated words collapse and at most six words are kept."""
    assert tokenize("Galaxy galaxy S23") == ("galaxy", "s23")
    assert len(tokenize("a b c d e f g h")) == 6


def test_numeric_only_query_is_a_token() -> None:
    """A bare number such as 9200 is a valid, scoreable query."""
    assert tokenize("9200") == ("9200",)
    assert score_text("9200", ("9200",), "Dimensity 9200") > 0


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("mtk", "mediatek"),
        ("MT6985", "dimensity 9200"),
        ("SM8550", "snapdragon 8 gen 2"),
        ("stock", "official"),
    ],
)
def test_synonyms_expand_both_ways(term: str, expected: str) -> None:
    """Vendor abbreviations and SoC part numbers resolve in both directions."""
    assert expected in expand(term)
    assert term.casefold() in expand(expected)


def test_synonym_match_ranks_below_literal_match() -> None:
    """A synonym hit never outranks the literal spelling."""
    literal = score_text("mediatek", ("mediatek",), "MediaTek Dimensity")
    synonym = score_text("mtk", ("mtk",), "MediaTek Dimensity")
    assert 0 < synonym < literal


def test_ranking_exact_prefix_word_substring_order() -> None:
    """Exact beats prefix beats word-prefix beats substring."""
    exact = score_text("galaxy", ("galaxy",), "Galaxy")
    prefix = score_text("galaxy", ("galaxy",), "Galaxy S23")
    word = score_text("galaxy", ("galaxy",), "Samsung Galaxy")
    substring = score_text("alaxy", ("alaxy",), "Samsung Galaxy")
    assert exact > prefix > word > substring > 0


def test_name_field_outranks_later_field() -> None:
    """A hit in the first (name) field beats the same hit in a later field."""
    first = score_text("zeph", ("zeph",), "Zephyr", "")
    later = score_text("zeph", ("zeph",), "Other", "Zephyr")
    assert first > later


def test_every_token_must_match() -> None:
    """One unmatched word rejects the row."""
    assert score_text("galaxy zzz", ("galaxy", "zzz"), "Galaxy S23") == 0.0


@pytest.mark.parametrize(
    ("left", "right", "limit", "expected"),
    [
        ("galaxy", "galaxy", 2, 0),
        ("galaxy", "galxy", 2, 1),
        ("galaxy", "galxay", 2, 1),
        ("samsung", "samung", 2, 1),
        ("abc", "xyzxyz", 1, 2),
    ],
)
def test_edit_distance_is_bounded_and_counts_transpositions(
    left: str, right: str, limit: int, expected: int
) -> None:
    """Transpositions cost one edit; distances beyond the limit are capped."""
    assert edit_distance(left, right, limit) == expected


def test_typos_are_never_forgiven_in_numbers_or_short_words() -> None:
    """S23 and S24, or 9200 and 9300, are different devices, not typos."""
    assert allowed_typos("9200") == 0
    assert allowed_typos("s23") == 0
    assert allowed_typos("sams") == 1
    assert allowed_typos("samsung") == 2
    assert fuzzy_match(("9300",), ("Dimensity 9200",)) is None


def test_fuzzy_match_finds_misspelt_word_and_reports_the_fix() -> None:
    """A misspelt word matches and the corrected spelling is reported."""
    match = fuzzy_match(("snapdargon",), ("Qualcomm", "Snapdragon 8 Gen 2"))
    assert match is not None
    assert match.replacements == {"snapdargon": "snapdragon"}
    assert corrected_query("Snapdargon 8", match.replacements) == "snapdragon 8"


def test_fuzzy_match_ignores_strict_matches() -> None:
    """Strict matches are the database's job, not the typo pass."""
    assert fuzzy_match(("snapdragon",), ("Snapdragon 8",)) is None


def test_fuzzy_match_requires_all_tokens() -> None:
    """A single unmatched word rejects the row even if another is a typo."""
    assert fuzzy_match(("snapdargon", "zzzzzz"), ("Snapdragon 8",)) is None


def test_similarity_bounds() -> None:
    """Similarity is 1 for equal strings and low for unrelated ones."""
    assert similarity("abc", "abc") == 1.0
    assert 0 <= similarity("abc", "xyz") < 0.5
