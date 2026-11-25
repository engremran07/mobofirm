"""Render-time processing of stored article HTML.

The stored body is sanitised on save, but ``QuerySet.update``, ``bulk_update`` and
fixtures bypass ``Post.save``. Everything that reaches a template therefore goes
through :func:`render_article`, which re-sanitises, adds heading anchors, builds a
table of contents and makes wide tables keyboard-scrollable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

from django.utils.html import strip_tags
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import slugify

from apps.core.editor import sanitize_stored_html

_HEADING = re.compile(r"<(h[23])((?:\s[^>]*)?)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_WORDS = re.compile(r"\w+")


@dataclass
class TocEntry:
    anchor: str
    text: str
    level: int


@dataclass
class RenderedArticle:
    html: SafeString
    toc: list[TocEntry] = field(default_factory=list)
    word_count: int = 0


def _anchor(text: str, used: set[str]) -> str:
    base = slugify(text)[:60] or "section"
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def render_article(body: str, *, min_toc_entries: int = 3) -> RenderedArticle:
    """Return sanitised article HTML with heading ids and a table of contents."""
    cleaned = sanitize_stored_html(body or "")
    used: set[str] = set()
    toc: list[TocEntry] = []

    def add_anchor(match: re.Match[str]) -> str:
        tag, attributes, inner = match.groups()
        text = strip_tags(inner).strip()
        if not text:
            return match.group(0)
        anchor = _anchor(text, used)
        toc.append(TocEntry(anchor=anchor, text=text, level=int(tag[1])))
        return f'<{tag}{attributes} id="{anchor}">{inner}</{tag}>'

    html = _HEADING.sub(add_anchor, cleaned)

    def wrap_table(match: re.Match[str]) -> str:
        table = match.group(0)
        first_row = re.search(r"<tr\b.*?</tr>", table, re.IGNORECASE | re.DOTALL)
        cells = (
            re.findall(r"<(?:th|td)\b", first_row.group(0), re.IGNORECASE)
            if first_row
            else []
        )
        table_modifier = (
            "facts" if len(cells) == 2 else "firmware" if len(cells) >= 6 else ""
        )
        modifier_class = f" article-table--{table_modifier}" if table_modifier else ""
        return (
            f'<div class="article-table{modifier_class}" role="region" tabindex="0" '
            f'aria-label="Scrollable table">{table}</div>'
        )

    html = _TABLE.sub(wrap_table, html)
    words = len(_WORDS.findall(strip_tags(cleaned)))
    return RenderedArticle(
        html=mark_safe(html),
        toc=toc if len(toc) >= min_toc_entries else [],
        word_count=words,
    )


def plain_excerpt(html: str, limit: int = 180) -> str:
    """Escape-safe plain text excerpt for cards and meta descriptions."""
    text = " ".join(unescape(strip_tags(html or "")).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"


__all__ = ["RenderedArticle", "TocEntry", "plain_excerpt", "render_article"]
