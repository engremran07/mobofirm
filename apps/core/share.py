"""Share links for public content: one builder for every page that offers sharing.

Only plain outbound links are produced (no third-party script or tracker is loaded).
Every link is built from one canonical absolute URL, percent-encoded exactly once;
the template escapes the finished ``href`` for HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit

TITLE_LIMIT = 200


@dataclass(frozen=True)
class ShareLink:
    """One destination in the share list."""

    label: str
    href: str
    icon: str

    @property
    def is_web(self) -> bool:
        """Web destinations open in a new tab; ``mailto:`` does not."""
        return self.href.startswith("https://")


def is_shareable_url(url: str) -> bool:
    """True for an absolute ``http(s)`` URL with a host and no embedded credentials."""
    parts = urlsplit(url)
    return (
        parts.scheme in {"http", "https"}
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


def _encode(value: str) -> str:
    return quote(value, safe="")


def share_links(url: str, title: str) -> list[ShareLink]:
    """Outbound share links for ``url``; an unusable URL yields no links."""
    if not is_shareable_url(url):
        return []
    text = " ".join(title.split())[:TITLE_LIMIT]
    encoded_url = _encode(url)
    encoded_text = _encode(text)
    return [
        ShareLink(
            "X",
            f"https://x.com/intent/post?url={encoded_url}&text={encoded_text}",
            "share-2",
        ),
        ShareLink(
            "Facebook",
            f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}",
            "share-2",
        ),
        ShareLink(
            "WhatsApp",
            f"https://wa.me/?text={_encode(f'{text} {url}'.strip())}",
            "message-circle",
        ),
        ShareLink(
            "Telegram",
            f"https://t.me/share/url?url={encoded_url}&text={encoded_text}",
            "send",
        ),
        ShareLink(
            "LinkedIn",
            f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_url}",
            "share-2",
        ),
        ShareLink(
            "Email", f"mailto:?subject={encoded_text}&body={encoded_url}", "mail"
        ),
    ]
