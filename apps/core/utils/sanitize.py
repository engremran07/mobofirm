from collections.abc import Iterable
from urllib.parse import urlsplit

import bleach

DEFAULT_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h2",
    "h3",
    "h4",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
}


def sanitize_html(
    value: str,
    *,
    allowed_tags: Iterable[str] | None = None,
    allowed_iframe_prefixes: Iterable[str] = (),
) -> str:
    iframe_prefixes = tuple(allowed_iframe_prefixes)
    tags = set(DEFAULT_ALLOWED_TAGS if allowed_tags is None else allowed_tags)
    attributes = {
        "a": ["href", "title", "rel"],
        "img": ["src", "alt", "title", "width", "height"],
        "iframe": ["src", "title", "width", "height", "allow", "allowfullscreen"],
    }
    if iframe_prefixes:
        tags.add("iframe")

    def allowed_attribute(tag: str, name: str, content: str) -> bool:
        if name not in attributes.get(tag, []):
            return False
        if tag == "iframe" and name == "src":
            parsed = urlsplit(content)
            return (
                parsed.scheme == "https"
                and bool(parsed.netloc)
                and any(content.startswith(prefix) for prefix in iframe_prefixes)
            )
        return True

    cleaner = bleach.Cleaner(
        tags=tags,
        attributes=allowed_attribute,
        protocols={"http", "https", "mailto"},
        strip=True,
        strip_comments=True,
    )
    return cleaner.clean(value or "")
