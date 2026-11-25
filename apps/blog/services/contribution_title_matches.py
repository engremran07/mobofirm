"""Bounded public-title suggestions for member blog contributions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from apps.blog import guide_requests
from apps.core.search.engine import search
from apps.core.search.text import clean_query

MIN_QUERY_LENGTH: Final = 3
MAX_MATCHES: Final = 8
CATALOGUE_KINDS: Final = ("brand", "model", "chipset", "tool", "firmware")
SEARCH_KINDS: Final = ("blog", *CATALOGUE_KINDS)
KIND_LABELS: Final = {
    "blog": "Published article or flashing guide",
    "brand": "Brand",
    "model": "Device model",
    "chipset": "Chipset",
    "tool": "Flashing tool",
    "firmware": "Approved firmware",
}


@dataclass(frozen=True, slots=True)
class TitleMatch:
    """A public result a contributor may inspect before submitting."""

    title: str
    url: str
    kind_label: str


def find_matches(title: str, *, user: object) -> list[TitleMatch]:
    """Find a bounded set of public blog, guide-request and catalogue title matches."""
    query = clean_query(title)
    if len(query) < MIN_QUERY_LENGTH:
        return []

    matches: dict[str, TitleMatch] = {}
    for duplicate in guide_requests.find_duplicates(query):
        label = (
            "Public guide request"
            if duplicate.kind == "request"
            else KIND_LABELS["blog"]
        )
        matches.setdefault(
            duplicate.url,
            TitleMatch(
                title=duplicate.title,
                url=duplicate.url,
                kind_label=label,
            ),
        )

    for hit in search(
        query,
        kinds=SEARCH_KINDS,
        limit=MAX_MATCHES,
        user=user,
    ):
        if not hit.url:
            continue
        kind_label = KIND_LABELS.get(hit.kind)
        if kind_label:
            matches.setdefault(
                hit.url,
                TitleMatch(title=hit.title, url=hit.url, kind_label=kind_label),
            )
        if len(matches) >= MAX_MATCHES:
            break
    return list(matches.values())[:MAX_MATCHES]
