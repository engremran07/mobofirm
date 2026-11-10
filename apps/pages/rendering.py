"""Safe rendering of CMS page content."""

from __future__ import annotations

import html
import importlib
from dataclasses import dataclass
from typing import Any

import markdown
from markdown.extensions.toc import slugify

from apps.core.editor.sanitizer import strip_srcless_embeds, video_embed_options

# Loaded dynamically: nh3 is a compiled extension that static analysis cannot read.
nh3: Any = importlib.import_module("nh3")

PAGE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "code",
        "dd",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
PAGE_ATTRIBUTES = {
    "*": {"class", "id", "title", "lang"},
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}
PAGE_URL_SCHEMES = frozenset({"http", "https", "mailto"})
TOC_DEPTH = "2-3"
WORDS_PER_MINUTE = 200


@dataclass(frozen=True)
class TocEntry:
    level: int
    anchor: str
    title: str


@dataclass(frozen=True)
class RenderedPage:
    html: str
    toc: tuple[TocEntry, ...]
    words: int

    @property
    def reading_minutes(self) -> int:
        return max(1, round(self.words / WORDS_PER_MINUTE))


def _heading_id(value: str, separator: str) -> str:
    # The prefix keeps generated anchors clear of the site shell's element ids.
    return "section-" + slugify(value, separator)


class _TocMarkdown(markdown.Markdown):
    """Markdown converter; the ``toc`` extension fills ``toc_tokens`` on convert."""

    toc_tokens: list[dict[str, Any]]


def _flatten(tokens: list[dict[str, Any]]) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for token in tokens:
        entries.append(TocEntry(token["level"], token["id"], token["name"]))
        entries.extend(_flatten(token.get("children", [])))
    return entries


def _sanitize(rendered: str) -> str:
    attributes = {tag: set(attrs) for tag, attrs in PAGE_ATTRIBUTES.items()}
    attributes["iframe"] = {"src", "title"}
    cleaned = nh3.clean(
        rendered,
        tags={*PAGE_TAGS, "iframe"},
        attributes=attributes,
        url_schemes=set(PAGE_URL_SCHEMES),
        link_rel="nofollow noopener",
        strip_comments=True,
        **video_embed_options(),
    )
    return strip_srcless_embeds(cleaned)


def render_page(content: str, content_format: str) -> RenderedPage:
    """Return sanitized HTML plus a table of contents for stored page content.

    Markdown passes raw HTML through, so both formats are sanitized with the same
    allowlist after conversion. Only Markdown headings get anchors and a contents list.
    """
    toc: tuple[TocEntry, ...] = ()
    rendered = content
    if content_format == "md":
        converter = _TocMarkdown(
            extensions=["extra", "sane_lists", "toc"],
            extension_configs={"toc": {"toc_depth": TOC_DEPTH, "slugify": _heading_id}},
        )
        rendered = converter.convert(content)
        toc = tuple(_flatten(converter.toc_tokens))
    clean = _sanitize(rendered)
    words = len(nh3.clean(clean, tags=set()).split())
    return RenderedPage(html=clean, toc=toc, words=words)


def render_page_content(content: str, content_format: str) -> str:
    """Return sanitized HTML for stored Markdown or HTML page content."""
    return render_page(content, content_format).html


def plain_text_excerpt(content: str, content_format: str, limit: int = 160) -> str:
    """Return a plain-text excerpt for meta descriptions."""
    text = html.unescape(
        nh3.clean(render_page_content(content, content_format), tags=set())
    )
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
