"""Comment-only rich-text rendering with safe external-link targets."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe

from apps.core.editor.sanitizer import render_rich_text

_VOID_ELEMENTS = frozenset({"br", "hr", "img"})


def _origin(value: str, default_scheme: str) -> tuple[str, int] | None:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower() or default_scheme
    if scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        return None
    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80
    return parsed.hostname.casefold(), port


def _is_external(href: str, host: str, secure: bool) -> bool:
    scheme = "https" if secure else "http"
    try:
        return _origin(href, scheme) not in {_origin(f"//{host}", scheme), None}
    except ValueError:
        return True


class _CommentLinkTargetParser(HTMLParser):
    """Add safe new-tab attributes to external links in sanitized comment HTML."""

    def __init__(self, host: str, secure: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.host = host
        self.secure = secure
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        href = attributes.get("href") or ""
        if tag == "a" and _is_external(href, self.host, self.secure):
            attributes["target"] = "_blank"
            rel = (attributes.get("rel") or "").split()
            for token in ("noopener", "noreferrer"):
                if token not in rel:
                    rel.append(token)
            attributes["rel"] = " ".join(rel)
        rendered_attrs = "".join(
            f' {escape(name)}="{escape(value or "")}"'
            for name, value in attributes.items()
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in _VOID_ELEMENTS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))


def render_comment_body(value: str | None, *, host: str, secure: bool) -> SafeString:
    """Sanitize comment HTML and open only off-site HTTP(S) links in a new tab."""
    parser = _CommentLinkTargetParser(host, secure)
    parser.feed(str(render_rich_text(value)))
    parser.close()
    return mark_safe("".join(parser.parts))
