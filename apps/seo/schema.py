"""JSON-LD builders. Every URL is absolute and every value comes from real data."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from django.utils.safestring import SafeString, mark_safe

CONTEXT = "https://schema.org"


def organization(
    *, name: str, url: str, logo: str = "", same_as: Sequence[str] = ()
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "Organization",
        "@id": f"{url}#organization",
        "name": name,
        "url": url,
    }
    if logo:
        node["logo"] = logo
    if same_as:
        node["sameAs"] = list(same_as)
    return node


def website(*, name: str, url: str, search_url_template: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "WebSite",
        "@id": f"{url}#website",
        "name": name,
        "url": url,
        "publisher": {"@id": f"{url}#organization"},
    }
    if search_url_template:
        node["potentialAction"] = {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": search_url_template},
            "query-input": "required name=search_term_string",
        }
    return node


def breadcrumb_list(crumbs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@context": CONTEXT,
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": index, "name": name, "item": url}
            for index, (name, url) in enumerate(crumbs, start=1)
        ],
    }


def _iso(value: datetime | date | None) -> str:
    return value.isoformat() if value else ""


def blog_posting(
    *,
    headline: str,
    url: str,
    description: str = "",
    image: str = "",
    published: datetime | None = None,
    modified: datetime | None = None,
    author_name: str = "",
    publisher_name: str = "",
    publisher_url: str = "",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "BlogPosting",
        "headline": headline[:110],
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "url": url,
    }
    if description:
        node["description"] = description
    if image:
        node["image"] = [image]
    if published:
        node["datePublished"] = _iso(published)
    if modified:
        node["dateModified"] = _iso(modified)
    if author_name:
        node["author"] = {"@type": "Person", "name": author_name}
    if publisher_name:
        node["publisher"] = {
            "@type": "Organization",
            "name": publisher_name,
            **({"url": publisher_url} if publisher_url else {}),
        }
    return node


def collection_page(
    *, name: str, url: str, description: str = "", items: Iterable[tuple[str, str]] = ()
) -> dict[str, Any]:
    """CollectionPage with an ItemList of real, linked entries."""
    entries = [
        {"@type": "ListItem", "position": index, "name": item_name, "url": item_url}
        for index, (item_name, item_url) in enumerate(items, start=1)
    ]
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "CollectionPage",
        "name": name,
        "url": url,
    }
    if description:
        node["description"] = description
    if entries:
        node["mainEntity"] = {
            "@type": "ItemList",
            "numberOfItems": len(entries),
            "itemListElement": entries,
        }
    return node


def web_page(
    *,
    name: str,
    url: str,
    description: str = "",
    published: datetime | None = None,
    modified: datetime | None = None,
    about_name: str = "",
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "WebPage",
        "name": name,
        "url": url,
    }
    if description:
        node["description"] = description
    if published:
        node["datePublished"] = _iso(published)
    if modified:
        node["dateModified"] = _iso(modified)
    if about_name:
        node["about"] = {"@type": "Thing", "name": about_name}
    return node


def software_application(
    *,
    name: str,
    url: str,
    description: str = "",
    operating_system: str = "",
    version: str = "",
    category: str = "UtilitiesApplication",
) -> dict[str, Any]:
    """Only for real applications (flashing tools), never for firmware archives."""
    node: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "SoftwareApplication",
        "name": name,
        "url": url,
        "applicationCategory": category,
    }
    if description:
        node["description"] = description
    if operating_system:
        node["operatingSystem"] = operating_system
    if version:
        node["softwareVersion"] = version
    return node


def to_json_ld(node: dict[str, Any]) -> SafeString:
    """Serialise for a ``<script type="application/ld+json">`` data block.

    ``<``, ``>`` and ``&`` are escaped so the payload can never close the script
    element; the block is data, so it is not subject to ``script-src``.
    """
    payload = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(char, escaped)
    return mark_safe(payload)
