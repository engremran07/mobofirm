"""Turns content into platform-native posts (pure: no database, no network).

Rules the copy follows, so it stays honest and useful:

* hook first, then one concrete detail, then the tracked link, then hashtags;
* urgency only states what is true ("just added" never appears on a re-share, which says
  "still useful"), and "free" is never claimed;
* gated content (paid / featured) gets a teaser and no download language;
* the link is never trimmed: hashtags go first, then the detail line, then the headline
  is shortened until the post fits the platform's counted length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from apps.distribution.channels import Adapter, visible_length
from apps.distribution.constants import (
    EMOJI_EXPRESSIVE,
    EMOJI_MINIMAL,
    EMOJI_NONE,
    HOOK_BENEFIT,
    HOOK_EVERGREEN,
    HOOK_HOWTO,
    HOOK_QUESTION,
    HOOK_URGENCY,
)
from apps.distribution.content import Content, teaser

HOOK_EMOJI = {
    HOOK_QUESTION: "❓",
    HOOK_BENEFIT: "✅",
    HOOK_URGENCY: "🆕",
    HOOK_HOWTO: "🛠️",
    HOOK_EVERGREEN: "♻️",
}
HASHTAG_MAX_CHARS = 30
TRENDING_EXTRA_TAGS = 2


@dataclass(frozen=True, slots=True)
class Draft:
    text: str
    hook: str
    hashtags: tuple[str, ...]


def to_hashtag(value: str) -> str:
    """``"Galaxy S23 Ultra"`` -> ``"GalaxyS23Ultra"``; empty when nothing usable."""
    cleaned = re.sub(r"\([^)]*\)", " ", value)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    tag = "".join(word[:1].upper() + word[1:] for word in words)[:HASHTAG_MAX_CHARS]
    return tag if tag and not tag.isdigit() else ""


def build_hashtags(
    content: Content, limit: int, *, trending: bool = False
) -> list[str]:
    """Smart tags from brand, model, chipset and content tags, bounded by ``limit``."""
    if limit <= 0:
        return []
    candidates: list[str] = [content.brand, content.model]
    if content.chipset:
        candidates.append(" ".join(content.chipset.split()[:3]))
    candidates.extend(content.tags)
    candidates.append("AndroidFirmware" if content.kind == "firmware" else "Android")
    if trending:
        candidates.insert(2, "NewRelease")
    total = limit + (TRENDING_EXTRA_TAGS if trending else 0)
    seen: set[str] = set()
    tags: list[str] = []
    for candidate in candidates:
        tag = to_hashtag(candidate)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(f"#{tag}")
        if len(tags) >= total:
            break
    return tags


def _device(content: Content) -> str:
    return " ".join(p for p in (content.brand, content.model) if p) or content.title


def _size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    gigabytes = size_bytes / 1024**3
    return f"{gigabytes:.1f} GB" if gigabytes >= 1 else f"{size_bytes / 1024**2:.0f} MB"


def headline(content: Content, hook: str, site: str) -> str:
    if content.kind == "blog":
        title = content.title
        return {
            HOOK_QUESTION: f"Ever wondered about {content.topic}?\n{title}"
            if content.topic
            else f"{title}?",
            HOOK_URGENCY: f"New on {site}: {title}",
            HOOK_HOWTO: f"Step by step: {title}",
            HOOK_EVERGREEN: f"From the archive: {title}",
        }.get(hook, title)
    device = _device(content)
    if hook == HOOK_EVERGREEN:
        return f"Still useful: {content.title} firmware."
    if hook == HOOK_QUESTION:
        tail = f" The {content.variant} build is on {site}." if content.variant else ""
        return f"Looking for {device} firmware?{tail}"
    if hook == HOOK_BENEFIT:
        return f"Restore or update your {device} with {content.variant or 'the latest'} firmware."
    if hook == HOOK_URGENCY:
        if content.trending:
            return f"New on {site}: {device} firmware is now available."
        return f"Just added: {content.title} firmware."
    if hook == HOOK_HOWTO:
        if content.has_guide:
            return f"How to flash the {device}: firmware and a step-by-step guide."
        return f"Flashing a {device}? Start with the right firmware."
    return content.title


def detail_line(content: Content, hook: str) -> str:
    if content.kind == "blog":
        if hook == HOOK_QUESTION and not content.summary:
            return ""
        text = teaser(content.summary, 160)
        if content.gated and text:
            return f"{text} (Featured article: preview)"
        return text
    parts = [
        f"Android {content.android_version}" if content.android_version else "",
        f"build {content.build_number}" if content.build_number else "",
        _size(content.size_bytes),
        " ".join(content.chipset.split()[:3]) if content.chipset else "",
    ]
    line = " · ".join(p for p in parts if p)
    if content.gated:
        line = f"{line} · featured file".strip(" ·")
    return line


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 1)].rstrip() + "…"


def compose(
    content: Content,
    adapter: Adapter,
    *,
    hook: str,
    link: str,
    site: str,
    emoji: str,
    hashtag_cap: int = 0,
    evergreen: bool = False,
) -> Draft:
    """The post text for ``adapter``, within its counted length."""
    limit = adapter.message_limit
    use_hook = HOOK_EVERGREEN if evergreen else hook
    head = headline(content, use_hook, site)
    detail = detail_line(content, use_hook)
    tag_limit = hashtag_cap or adapter.hashtag_limit
    tags = build_hashtags(
        content, tag_limit, trending=content.trending and not evergreen
    )
    if adapter.emoji_ok and emoji != EMOJI_NONE and HOOK_EMOJI.get(use_hook):
        head = f"{HOOK_EMOJI[use_hook]} {head}"
    pointer = "👉 " if adapter.emoji_ok and emoji == EMOJI_EXPRESSIVE else ""
    if emoji == EMOJI_MINIMAL:
        pointer = ""

    def assemble() -> str:
        lines = [head]
        if detail:
            lines.append(detail)
        if link:
            lines.append(f"{pointer}{link}")
        if tags:
            lines.append(" ".join(tags))
        return "\n".join(lines)

    text = assemble()
    while visible_length(text, adapter.url_length) > limit:
        if tags:
            tags.pop()
        elif detail:
            detail = ""
        else:
            overflow = visible_length(text, adapter.url_length) - limit
            if len(head) <= 12:
                break
            head = _shorten(head, max(len(head) - overflow, 12))
        text = assemble()
    return Draft(text=text, hook=use_hook, hashtags=tuple(tags))
