"""Deterministic keyword extraction and tag matching for blog content.

No network or AI service is involved: candidate tags come from the post's own
title, summary and body, and are matched against the curated tag vocabulary.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from django.db import IntegrityError, transaction
from django.utils.html import strip_tags
from django.utils.text import slugify

from apps.tags.models import Tag
from apps.tags.selectors import public_tags

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "get",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "more",
        "most",
        "new",
        "no",
        "not",
        "of",
        "on",
        "one",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "up",
        "use",
        "used",
        "using",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "while",
        "who",
        "will",
        "with",
        "without",
        "you",
        "your",
        "guide",
        "download",
        "firmware",
    ]
)
_ENTITY = re.compile(
    r"\b(?:[A-Z][\w+-]*|\d[\w+-]*)(?:\s+(?:[A-Z][\w+-]*|\d[\w+-]*)){0,3}\b"
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+-]*")
MAX_NAME_LENGTH = 64


def normalize(text: str) -> str:
    return " ".join((text or "").lower().strip().split())


def post_text(post: Any) -> str:
    return " ".join(
        [
            getattr(post, "title", "") or "",
            getattr(post, "summary", "") or "",
            strip_tags(getattr(post, "body", "") or "")[:4000],
        ]
    )


def suggest_tags_from_text(text: str, limit: int = 10) -> list[dict[str, str]]:
    """Rank capitalised entities and frequent keywords found in ``text``."""
    text = (text or "").strip()
    if not text:
        return []
    scores: Counter[str] = Counter()
    display: dict[str, str] = {}

    for match in _ENTITY.finditer(text):
        phrase = match.group(0).strip()
        norm = normalize(phrase)
        words = norm.split()
        if (
            len(norm) < 3
            or len(norm) > MAX_NAME_LENGTH
            or all(word in STOPWORDS for word in words)
        ):
            continue
        scores[norm] += 2 + len(words)
        display.setdefault(norm, phrase)

    for token, count in Counter(_TOKEN.findall(text.lower())).items():
        if len(token) >= 4 and token not in STOPWORDS and count >= 2:
            scores[token] += count
            display.setdefault(token, token.title())

    ranked = [name for name, _score in scores.most_common(limit)]
    return [
        {"name": display[norm], "normalized": norm, "score": str(scores[norm])}
        for norm in ranked
    ]


def curated_matches(text: str) -> list[Tag]:
    """Curated tags whose name or synonym appears in ``text``."""
    haystack = f" {normalize(strip_tags(text))} "
    matches: list[Tag] = []
    for tag in public_tags():
        names = {tag.normalized_name or normalize(tag.name)}
        names.update(
            normalize(item) for item in (tag.synonyms or []) if isinstance(item, str)
        )
        if any(name and f" {name} " in haystack for name in names):
            matches.append(tag)
    return matches


def get_or_create_suggestion(name: str) -> Tag | None:
    """Return the tag for ``name``, creating an uncurated suggestion if unknown."""
    name = " ".join((name or "").split())[:MAX_NAME_LENGTH]
    norm = normalize(name)
    slug = slugify(norm)[:80]
    if not norm or not slug:
        return None
    existing = (
        Tag.objects.filter(
            normalized_name=norm, merge_into__isnull=True, is_deleted=False
        ).first()
        or Tag.objects.filter(slug=slug, is_deleted=False).first()
    )
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return Tag.objects.create(
                name=name,
                normalized_name=norm,
                slug=slug,
                ai_suggested=True,
                is_curated=False,
            )
    except IntegrityError:
        return Tag.objects.filter(slug=slug).first()


@transaction.atomic
def auto_tag_post(post: Any, allow_create: bool = True, max_tags: int = 5) -> list[Tag]:
    """Attach matching curated tags; record unknown keywords as staff suggestions."""
    text = post_text(post)
    attached: list[Tag] = []
    seen: set[int] = set()
    for suggestion in suggest_tags_from_text(text, limit=max_tags * 2):
        tag = Tag.objects.filter(
            normalized_name=suggestion["normalized"],
            is_deleted=False,
            merge_into__isnull=True,
        ).first()
        if tag is None:
            if allow_create:
                get_or_create_suggestion(suggestion["name"])
            continue
        if not (tag.is_curated and tag.is_active) or tag.pk in seen:
            continue
        seen.add(tag.pk)
        post.tags.add(tag)
        attached.append(tag)
        if len(attached) >= max_tags:
            break
    return attached
