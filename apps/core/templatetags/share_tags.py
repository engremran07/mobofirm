"""``{% share_widget %}``: the one share component (blog articles, firmware pages)."""

from __future__ import annotations

from hashlib import sha1
from typing import Any

from django import template

from apps.core.share import share_links

register = template.Library()


@register.inclusion_tag("includes/share.html", takes_context=True)
def share_widget(
    context: template.Context,
    url: str,
    title: str,
    *,
    text: str = "",
    heading: str = "Share this page",
    copy_message: str = "Link copied.",
) -> dict[str, Any]:
    """Share buttons for ``url`` (the canonical absolute URL) titled ``title``.

    A relative path is made absolute with the request; anything that is not an
    ``http(s)`` URL renders nothing instead of a broken control.

    Usage: ``{% share_widget share_url post.title heading="Share this article" %}``.
    """
    request = context.get("request")
    target = str(url or "")
    if target.startswith("/") and not target.startswith("//") and request is not None:
        target = request.build_absolute_uri(target)
    name = str(title or "").strip()
    links = share_links(target, name)
    return {
        "links": links,
        "url": target,
        "title": name,
        "text": text,
        "heading": heading,
        "heading_id": f"share-{sha1(target.encode()).hexdigest()[:8]}",
        "copy_message": copy_message,
    }
