"""HTML sanitising shared by the blog and comments apps.

The allow-list matches what the strict CSP can actually render: no inline styles,
no iframes/embeds (``frame-src`` is ``'none'``) and images only from the site's own
origin (``img-src 'self'``). Anything outside that list is removed rather than left
to be blocked silently by the browser.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

import nh3
from django.utils.text import slugify as django_slugify

DEFAULT_TAGS = frozenset(
    {
        "p",
        "br",
        "hr",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "sub",
        "sup",
        "ul",
        "ol",
        "li",
        "a",
        "code",
        "pre",
        "kbd",
        "blockquote",
        "h2",
        "h3",
        "h4",
        "h5",
        "figure",
        "figcaption",
        "img",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "div",
        "span",
    }
)

DEFAULT_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class"},
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height", "loading"},
    "th": {"scope", "colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "ol": {"start"},
}

_IMG_WITHOUT_SRC = re.compile(r"<img(?![^>]*\ssrc=)[^>]*>", re.IGNORECASE)


def slugify(
    value: str, allow_unicode: bool = False, max_length: int | None = None
) -> str:
    slug = django_slugify(value, allow_unicode=allow_unicode)
    if max_length:
        slug = slug[:max_length]
    return slug


def is_same_origin_path(value: str) -> bool:
    """True for an absolute-path URL such as ``/media/x.png`` (not ``//host``)."""
    return value.startswith("/") and not value.startswith("//")


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    if tag == "img" and attribute == "src":
        return value if is_same_origin_path(value) else None
    return value


def sanitize_html(
    html: str,
    allowed_tags: Iterable[str] | None = None,
    allowed_attrs: dict[str, Any] | None = None,
) -> str:
    """Return ``html`` reduced to the CSP-compatible allow-list."""
    if not html:
        return ""
    tags = set(allowed_tags) if allowed_tags is not None else set(DEFAULT_TAGS)
    raw_attrs = allowed_attrs if allowed_attrs is not None else DEFAULT_ATTRIBUTES
    attributes = {name: set(values) for name, values in raw_attrs.items()}
    attribute_filter: Callable[[str, str, str], str | None] = _attribute_filter
    cleaned = nh3.clean(
        html,
        tags=tags,
        attributes=attributes,
        attribute_filter=attribute_filter,
        strip_comments=True,
        url_schemes={"http", "https", "mailto"},
    )
    return _IMG_WITHOUT_SRC.sub("", cleaned)
