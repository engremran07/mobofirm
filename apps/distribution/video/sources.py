"""What a video is about: one neutral ``Source`` built from the existing content contracts.

Firmware and posts come from ``apps.distribution.content`` (the same public rules as
sharing: gated items give a teaser only). Extra facts a post offers (steps, headings,
reading time) are read through ``django.apps`` model lookup, never by importing the blog.
Roundups and spotlights use the public feeds module of this app.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from html.parser import HTMLParser

from django.apps import apps
from django.utils import timezone

from apps.distribution import content as content_module
from apps.distribution import feeds
from apps.seo.siteurls import absolute_url

KIND_ROUNDUP = "roundup"
KIND_SPOTLIGHT = "spotlight"
VIDEO_KINDS = ("firmware", "blog", KIND_ROUNDUP, KIND_SPOTLIGHT)
MAX_STEPS = 5
MAX_POINTS = 4
_WEEK = re.compile(r"^week:\d{4}-W\d{2}$")
_SCOPE = re.compile(r"^(brand|chipset):[a-z0-9][a-z0-9_-]{0,138}$")


@dataclass(frozen=True, slots=True)
class Source:
    kind: str
    ref: str
    title: str
    url: str
    summary: str = ""
    brand: str = ""
    model: str = ""
    variant: str = ""
    chipset: str = ""
    android_version: str = ""
    build_number: str = ""
    size_bytes: int = 0
    tags: tuple[str, ...] = ()
    image_url: str = ""
    is_guide: bool = False
    gated: bool = False
    reading_minutes: int = 0
    points: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    items: tuple[tuple[str, str], ...] = ()
    bars: tuple[tuple[str, int], ...] = ()

    @property
    def device(self) -> str:
        return " ".join(p for p in (self.brand, self.model) if p) or self.title

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 1) if self.size_bytes else 0.0


class _ListReader(HTMLParser):
    """Collects ordered-list items and headings as plain text (tags are dropped)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.steps: list[str] = []
        self.headings: list[str] = []
        self._ol = 0
        self._target: list[str] | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "ol":
            self._ol += 1
        elif tag == "li" and self._ol:
            self._target, self._buffer = self.steps, []
        elif tag in {"h2", "h3"}:
            self._target, self._buffer = self.headings, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "ol":
            self._ol = max(0, self._ol - 1)
        if tag in {"li", "h2", "h3"} and self._target is not None:
            text = " ".join("".join(self._buffer).split())
            if text:
                self._target.append(text)
            self._target = None

    def handle_data(self, data: str) -> None:
        if self._target is not None:
            self._buffer.append(data)


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 12]


def _post_extras(
    ref: str, gated: bool, summary: str
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    """``(reading minutes, key points, steps)``; a gated post offers only its teaser."""
    points = tuple(_short(s, 90) for s in _sentences(summary)[:MAX_POINTS])
    if gated:
        return 0, points, ()
    try:
        post = (
            apps.get_model("blog", "Post")
            .published()
            .filter(pk=ref.partition(":")[2])
            .first()
        )
    except (LookupError, ValueError):
        post = None
    if post is None:
        return 0, points, ()
    reader = _ListReader()
    reader.feed(str(post.body or ""))
    heads = tuple(_short(h, 90) for h in reader.headings[:MAX_POINTS])
    steps = tuple(_short(s, 95) for s in reader.steps[:MAX_STEPS])
    return int(getattr(post, "reading_time", 0) or 0), heads or points, steps


def from_content(item: content_module.Content) -> Source:
    minutes, points, steps = (0, (), ())
    if item.kind == "blog":
        minutes, points, steps = _post_extras(item.object_ref, item.gated, item.summary)
    return Source(
        kind=item.kind,
        ref=item.object_ref,
        title=item.title,
        url=item.url,
        summary=item.summary if item.kind == "blog" else "",
        brand=item.brand,
        model=item.model,
        variant=item.variant,
        chipset=item.chipset,
        android_version=item.android_version,
        build_number=item.build_number,
        size_bytes=item.size_bytes,
        tags=item.tags,
        image_url=item.image_url,
        is_guide=item.is_guide,
        gated=item.gated,
        reading_minutes=minutes,
        points=points,
        steps=steps,
    )


def _roundup(ref: str) -> Source | None:
    if not _WEEK.match(ref):
        return None
    since = timezone.now() - timedelta(days=7)
    rows = [i for i in feeds.firmware_items(60) if i.published >= since]
    if not rows:
        return None
    counts: dict[str, int] = {}
    for row in rows:
        brand = row.categories[0] if row.categories else "Other"
        counts[brand] = counts.get(brand, 0) + 1
    bars = tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
    items = tuple(
        (
            _short(r.title.removesuffix(" firmware"), 48),
            r.categories[2] if len(r.categories) > 2 else "",
        )
        for r in rows[:4]
    )
    return Source(
        KIND_ROUNDUP,
        ref,
        "This week's firmware",
        absolute_url("/"),
        summary=f"{len(rows)} new verified firmware",
        items=items,
        bars=bars,
    )


def _spotlight(ref: str) -> Source | None:
    if not _SCOPE.match(ref):
        return None
    scope, _sep, slug = ref.partition(":")
    kwargs = {scope: slug}
    rows = feeds.firmware_items(30, **kwargs)
    if not rows:
        return None
    label = slug.replace("-", " ").title()
    items = tuple((_short(r.title.removesuffix(" firmware"), 48), "") for r in rows[:4])
    return Source(
        KIND_SPOTLIGHT,
        ref,
        f"{label} spotlight",
        absolute_url("/"),
        summary=f"{len(rows)} verified firmware",
        items=items,
        bars=(("Firmware", len(rows)),),
        brand=label if scope == "brand" else "",
        chipset=label if scope == "chipset" else "",
    )


def load(kind: str, ref: str) -> Source | None:
    """The source of a video, or ``None`` when the content is no longer public."""
    if kind in {"firmware", "blog"}:
        item = content_module.load(kind, ref)
        return from_content(item) if item is not None else None
    if kind == KIND_ROUNDUP:
        return _roundup(ref)
    if kind == KIND_SPOTLIGHT:
        return _spotlight(ref)
    return None
