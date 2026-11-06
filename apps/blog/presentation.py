"""Article-page presentation helpers (SEO metadata comes from ``apps.seo``)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class ShareLink:
    label: str
    href: str
    icon: str


def share_links(url: str, title: str) -> list[ShareLink]:
    """Plain outbound links; no third-party script or tracker is loaded."""
    encoded_url = quote(url, safe="")
    encoded_title = quote(title, safe="")
    return [
        ShareLink(
            "X",
            f"https://twitter.com/intent/tweet?url={encoded_url}&text={encoded_title}",
            "share-2",
        ),
        ShareLink(
            "Facebook",
            f"https://www.facebook.com/sharer/sharer.php?u={encoded_url}",
            "share-2",
        ),
        ShareLink(
            "LinkedIn",
            f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_url}",
            "share-2",
        ),
        ShareLink(
            "WhatsApp",
            f"https://wa.me/?text={encoded_title}%20{encoded_url}",
            "message-circle",
        ),
        ShareLink(
            "Telegram",
            f"https://t.me/share/url?url={encoded_url}&text={encoded_title}",
            "send",
        ),
        ShareLink(
            "Email", f"mailto:?subject={encoded_title}&body={encoded_url}", "mail"
        ),
    ]
