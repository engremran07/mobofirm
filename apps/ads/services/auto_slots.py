"""Automatic ad slots: in-article paragraphs and in-feed grid tiles.

Both build on the zone system (``apps.ads.services.zones``): a slot is just a zone
(``in_article``, ``between_items``) whose *position* is detected from the content instead
of being hand-placed in a template. The placement (data, edited in the placement manager)
still decides whether the zone is on for a page, which campaign fills it and for whom;
``AdPlacement.auto_*`` only tune the spacing.

Article HTML is never regenerated: it is scanned with ``html.parser`` for top-level
blocks and the slot markup is spliced in at offsets between two text paragraphs, so the
original markup stays byte-for-byte intact. HTML that does not parse cleanly (unbalanced
tags) gets no ads at all rather than risking a broken page.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser

from apps.ads.models import AdPlacement

IN_ARTICLE = "in_article"
BETWEEN_ITEMS = "between_items"
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
RAW_TEXT_TAGS = frozenset({"script", "style"})
# Hard limits that no placement setting can lower: never an ad in the first paragraph,
# never two ads back to back.
MIN_FIRST_AFTER = 2
MIN_EVERY = 2


@dataclass(frozen=True, slots=True)
class AutoRule:
    """Spacing of automatic slots: first after K, every N, at most M, needing >= min."""

    first_after: int
    every: int
    max_slots: int
    min_items: int


DEFAULT_RULES: dict[str, AutoRule] = {
    IN_ARTICLE: AutoRule(first_after=3, every=4, max_slots=3, min_items=5),
    # A tile ad follows tile 8 and 16; it needs a tile after it, so a grid needs 9+.
    BETWEEN_ITEMS: AutoRule(first_after=8, every=8, max_slots=2, min_items=9),
}


def rule_for(placement: AdPlacement | None, zone_code: str) -> AutoRule:
    """The placement's own spacing where set (non-zero), else the zone default."""
    default = DEFAULT_RULES.get(zone_code, DEFAULT_RULES[BETWEEN_ITEMS])
    if placement is None:
        return default
    first_after = placement.auto_first_after or default.first_after
    every = placement.auto_every or default.every
    return AutoRule(
        first_after=max(first_after, MIN_FIRST_AFTER),
        every=max(every, MIN_EVERY),
        max_slots=placement.auto_max or default.max_slots,
        min_items=placement.auto_min_items or default.min_items,
    )


def tile_slots(total: int, rule: AutoRule, *, cap: int | None = None) -> list[int]:
    """Tile counts (1-based) after which a sponsored tile goes.

    Count-aware: nothing for fewer than ``min_items`` tiles, and a slot needs at least
    one real tile after it, so an ad is never the last (orphan) tile of a page.
    """
    limit = rule.max_slots if cap is None else min(rule.max_slots, cap)
    if limit <= 0 or total < max(rule.min_items, rule.first_after + 1):
        return []
    slots: list[int] = []
    position = rule.first_after
    while position < total and len(slots) < limit:
        slots.append(position)
        position += rule.every
    return slots


# ------------------------------------------------------------- article HTML ----


@dataclass(slots=True)
class Block:
    """One top-level element of the article and where it sits in the source."""

    tag: str
    start: int
    end: int = 0
    words: int = 0
    has_text: bool = False
    after_loose_text: bool = False


@dataclass(slots=True)
class _State:
    stack: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    current: Block | None = None
    loose_text: bool = False
    malformed: bool = False


class _BlockScanner(HTMLParser):
    """Find top-level blocks with exact source offsets (no re-serialisation)."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.state = _State()
        self._raw_text_depth = 0

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        state = self.state
        start = self._offset()
        if tag in VOID_TAGS:
            if not state.stack:
                width = len(self.get_starttag_text() or "")
                self._add(Block(tag, start, start + width))
            return
        if not state.stack:
            state.current = Block(tag, start)
            state.current.after_loose_text = state.loose_text
            state.loose_text = False
        state.stack.append(tag)
        if tag in RAW_TEXT_TAGS:
            self._raw_text_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        state = self.state
        if not state.stack:
            start = self._offset()
            width = len(self.get_starttag_text() or "")
            self._add(Block(tag, start, start + width))

    def handle_endtag(self, tag: str) -> None:
        state = self.state
        if tag in VOID_TAGS:
            return
        if not state.stack or state.stack[-1] != tag:
            state.malformed = True
            return
        state.stack.pop()
        if tag in RAW_TEXT_TAGS:
            self._raw_text_depth -= 1
        if not state.stack and state.current is not None:
            close = self.source.find(">", self._offset())
            if close < 0:
                state.malformed = True
                return
            state.current.end = close + 1
            self._add(state.current)
            state.current = None

    def handle_data(self, data: str) -> None:
        state = self.state
        if self._raw_text_depth or not data.strip():
            return
        if state.current is not None:
            state.current.has_text = True
            state.current.words += len(data.split())
        elif not state.stack:
            state.loose_text = True

    def _add(self, block: Block) -> None:
        block.after_loose_text = block.after_loose_text or self.state.loose_text
        self.state.loose_text = False
        self.state.blocks.append(block)


def scan_blocks(source: str) -> list[Block] | None:
    """Top-level blocks of ``source``; ``None`` when the markup is not well formed."""
    scanner = _BlockScanner(source)
    try:
        scanner.feed(source)
        scanner.close()
    except (AssertionError, ValueError):
        return None
    state = scanner.state
    if state.malformed or state.stack or state.current is not None:
        return None
    return state.blocks


def _is_text_paragraph(block: Block) -> bool:
    return block.tag == "p" and block.has_text


def paragraph_count(blocks: list[Block]) -> int:
    return sum(1 for block in blocks if _is_text_paragraph(block))


def plan_article_slots(
    blocks: list[Block],
    rule: AutoRule,
    *,
    min_words_between: int = 0,
    cap: int | None = None,
) -> list[int]:
    """Source offsets where a slot goes.

    A slot goes after a text paragraph when (a) it is the K-th paragraph (first slot) or
    N paragraphs after the previous slot, (b) the next top-level block is another text
    paragraph, so the slot never sits before a heading, list, table, figure, quote or
    code block, nor splits a paragraph from the list it introduces, and (c) at least
    ``min_words_between`` words of content separate two slots. A candidate that fails
    (b) or (c) moves to the next paragraph. Short articles (fewer than ``min_items``
    paragraphs) get none.
    """
    limit = rule.max_slots if cap is None else min(rule.max_slots, cap)
    if limit <= 0 or paragraph_count(blocks) < rule.min_items:
        return []
    offsets: list[int] = []
    paragraphs_since = 0
    words_since = 0
    for index, block in enumerate(blocks):
        words_since += block.words
        if not _is_text_paragraph(block):
            continue
        paragraphs_since += 1
        threshold = rule.every if offsets else rule.first_after
        if paragraphs_since < threshold:
            continue
        if offsets and words_since < min_words_between:
            continue
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if (
            following is None
            or not _is_text_paragraph(following)
            or following.after_loose_text
        ):
            continue
        offsets.append(block.end)
        paragraphs_since = 0
        words_since = 0
        if len(offsets) >= limit:
            break
    return offsets


def inject_slots(
    source: str,
    rule: AutoRule,
    render_slot: Callable[[], str],
    *,
    min_words_between: int = 0,
    cap: int | None = None,
) -> str:
    """``source`` with ``render_slot()`` output spliced in at the planned offsets.

    ``render_slot`` returns '' when no ad is available (consent, caps, no creative); the
    first empty answer stops the insertion, since later slots would be empty as well.
    """
    blocks = scan_blocks(source)
    if not blocks:
        return source
    offsets = plan_article_slots(
        blocks, rule, min_words_between=min_words_between, cap=cap
    )
    if not offsets:
        return source
    pieces: list[str] = []
    cursor = 0
    for offset in offsets:
        markup = render_slot()
        if not markup:
            break
        pieces.append(source[cursor:offset])
        pieces.append(markup)
        cursor = offset
    pieces.append(source[cursor:])
    return "".join(pieces)
