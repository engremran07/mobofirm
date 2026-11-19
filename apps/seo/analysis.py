"""What a page really serves: outline, images, head tags and structured data.

The screen shows findings *in context*. The page is rendered in-process (the same
request handler visitors reach, flagged as the internal crawler so no counter moves)
and parsed with the standard library; nothing is fetched over the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from .fetcher import FetchError, FetchPolicy
from .inprocess import InProcessFetcher
from .inventory import serving_host
from .siteurls import site_origin

TITLE_PIXEL_CHARS = 60
DESCRIPTION_PIXEL_CHARS = 160
CONTENT_ROOTS = frozenset({"main", "article"})


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class PageImage:
    src: str
    alt: str | None  # ``None`` when the attribute is missing


@dataclass
class PageAnalysis:
    status: int = 0
    error: str = ""
    title: str = ""
    description: str = ""
    canonical: str = ""
    robots: list[str] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    images: list[PageImage] = field(default_factory=list)
    open_graph: dict[str, str] = field(default_factory=dict)
    twitter: dict[str, str] = field(default_factory=dict)
    schema_nodes: list[dict[str, Any]] = field(default_factory=list)
    internal_links: int = 0
    nofollow_links: int = 0

    @property
    def h1_count(self) -> int:
        return sum(1 for heading in self.headings if heading.level == 1)

    @property
    def schema_types(self) -> list[str]:
        return [
            str(node.get("@type", ""))
            for node in self.schema_nodes
            if node.get("@type")
        ]

    @property
    def missing_alt(self) -> list[PageImage]:
        return [image for image in self.images if image.alt is None]


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = PageAnalysis()
        self._text_target: str | None = None
        self._buffer: list[str] = []
        self._ld: list[str] | None = None
        self._root_depth = 0
        self.all_images: list[PageImage] = []
        self.root_images: list[PageImage] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        page = self.result
        if tag in CONTENT_ROOTS:
            self._root_depth += 1
        if tag == "title" or (len(tag) == 2 and tag[0] == "h" and tag[1] in "123456"):
            self._text_target, self._buffer = tag, []
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "")
            if name == "description":
                page.description = content
            elif name == "robots":
                page.robots.append(content)
            elif name.startswith("og:"):
                page.open_graph[name] = content
            elif name.startswith("twitter:"):
                page.twitter[name] = content
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            page.canonical = values.get("href", "")
        elif tag == "a":
            href = values.get("href", "")
            if href.startswith("/") and not href.startswith("//"):
                page.internal_links += 1
                if "nofollow" in values.get("rel", "").lower():
                    page.nofollow_links += 1
        elif tag == "img":
            image = PageImage(values.get("src", ""), values.get("alt"))
            self.all_images.append(image)
            if self._root_depth:
                self.root_images.append(image)
        elif (
            tag == "script" and values.get("type", "").lower() == "application/ld+json"
        ):
            self._ld = []

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
        elif self._text_target:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        page = self.result
        if tag in CONTENT_ROOTS and self._root_depth:
            self._root_depth -= 1
        if tag == self._text_target:
            text = " ".join("".join(self._buffer).split())
            if tag == "title":
                page.title = text
            else:
                page.headings.append(Heading(int(tag[1]), text))
            self._text_target = None
        elif tag == "script" and self._ld is not None:
            try:
                data = json.loads("".join(self._ld))
            except json.JSONDecodeError:
                data = None
            nodes = data if isinstance(data, list) else [data]
            page.schema_nodes.extend(node for node in nodes if isinstance(node, dict))
            self._ld = None


def analyse(path: str) -> PageAnalysis:
    """Render ``path`` in-process and describe what it serves."""
    origin = site_origin() or f"http://{serving_host()}"
    clean = path if path.startswith("/") else f"/{urlsplit(path).path}"
    try:
        result = InProcessFetcher(policy=FetchPolicy.from_settings()).fetch(
            f"{origin}{clean}", follow_redirects=False
        )
    except FetchError as error:
        return PageAnalysis(error=str(error))
    parser = _Parser()
    parser.feed(result.text)
    page = parser.result
    page.status = result.status
    page.images = parser.root_images or parser.all_images
    return page


@dataclass(frozen=True)
class SerpPreview:
    title: str
    url: str
    description: str
    title_too_long: bool
    description_too_long: bool


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def serp_preview(title: str, description: str, url: str) -> SerpPreview:
    """How a result would look: titles/descriptions are cut where engines cut them."""
    return SerpPreview(
        title=_cut(title, TITLE_PIXEL_CHARS),
        url=url,
        description=_cut(description, DESCRIPTION_PIXEL_CHARS),
        title_too_long=len(title) > TITLE_PIXEL_CHARS,
        description_too_long=len(description) > DESCRIPTION_PIXEL_CHARS,
    )
