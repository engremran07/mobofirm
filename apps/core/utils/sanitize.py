from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from django.utils.text import slugify as django_slugify

try:
    import nh3  # type: ignore
except Exception:  # pragma: no cover
    nh3 = None


def slugify(
    value: str, allow_unicode: bool = False, max_length: int | None = None
) -> str:
    slug = django_slugify(value, allow_unicode=allow_unicode)
    if max_length:
        slug = slug[:max_length]
    return slug


def sanitize_html(
    html: str,
    allowed_tags: Iterable[str] | None = None,
    allowed_attrs: dict[str, Any] | None = None,
    allowed_iframe_prefixes: Iterable[str] | None = None,
) -> str:
    if not html:
        return ""
    if nh3 is None:
        # fallback: strip tags by regex
        return re.sub(r"<[^>]+>", "", html)
    tags = set(
        allowed_tags
        or [
            "p",
            "br",
            "strong",
            "em",
            "ul",
            "ol",
            "li",
            "a",
            "code",
            "blockquote",
            "h2",
            "h3",
            "h4",
            "h5",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "div",
            "span",
            "iframe",
        ]
    )

    # Convert attrs to nh3 format (dict of sets)
    # NOTE: Do NOT include "rel" for <a> — nh3 manages rel internally
    raw_attrs = allowed_attrs or {
        "a": ["href", "title", "target"],
        "div": ["style", "class"],
        "span": ["style", "class"],
        "td": ["style", "class"],
        "th": ["style", "class"],
        "tr": ["style", "class"],
        "table": ["style", "class"],
        "h2": ["style", "class"],
        "h3": ["style", "class"],
        "p": ["style", "class"],
        "iframe": [
            "src",
            "width",
            "height",
            "frameborder",
            "allow",
            "allowfullscreen",
            "class",
            "title",
        ],
    }
    attrs = {k: set(v) for k, v in raw_attrs.items()}

    cleaned = nh3.clean(
        html,
        tags=tags,
        attributes=attrs,
        strip_comments=True,
        url_schemes={"http", "https", "mailto"},
    )

    # Drop iframes not on the allowed prefix list
    result = cleaned
    if "iframe" in tags:
        prefixes = tuple(
            allowed_iframe_prefixes
            or (
                "https://www.youtube.com/embed/",
                "https://www.youtube-nocookie.com/embed/",
                "https://player.vimeo.com/video/",
                "https://player.vimeo.com/",
            )
        )

        def _strip_invalid_iframes(match):
            src = match.group(1) or ""
            # Normalize protocol-relative URLs to https
            if src.startswith("//"):
                src = "https:" + src
            # Update the match to use normalized src
            full_tag = match.group(0).replace(match.group(1), src)
            return full_tag if src.startswith(prefixes) else ""

        result = re.sub(
            r'<iframe[^>]+src="([^"]+)"[^>]*></iframe>',
            _strip_invalid_iframes,
            result,
            flags=re.IGNORECASE,
        )
    return result
