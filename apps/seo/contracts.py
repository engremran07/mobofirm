"""The single metadata contract shared by every page provider and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

POLICY_VERSION = "2026.11.1"

PAGE_TYPES = frozenset(
    {
        "home",
        "brand",
        "brand_list",
        "model",
        "chipset",
        "chipset_list",
        "firmware",
        "firmware_category",
        "firmware_browse",
        "tools_hub",
        "tool",
        "guides_hub",
        "blog_list",
        "blog_post",
        "blog_category",
        "blog_archive",
        "blog_brand",
        "blog_model",
        "tag_list",
        "tag",
        "page",
        "pages_index",
        "search",
        "private",
        "unmapped",
    }
)


@dataclass(frozen=True)
class Crumb:
    name: str
    path: str


@dataclass
class PageSEO:
    """What a provider knows about one page. Empty fields fall through to defaults.

    Providers must only return facts that exist in the database or the route; never
    invented dates, specifications or checksums.
    """

    page_type: str
    title: str = ""
    description: str = ""
    path: str = ""
    canonical: str = ""
    index: bool = True
    follow: bool = True
    noindex_reason: str = ""
    image: str = ""
    og_type: str = "website"
    crumbs: list[Crumb] = field(default_factory=list)
    schema: list[dict[str, Any]] = field(default_factory=list)
    published: datetime | None = None
    modified: datetime | None = None
    alternates: dict[str, str] = field(default_factory=dict)
    object_ref: tuple[str, str] | None = None
    full_title: bool = False
    sources: dict[str, str] = field(default_factory=dict)
    # Query parameters that identify distinct content and stay in the canonical.
    canonical_params: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedSEO:
    """Immutable, validated head contract for one response."""

    page_type: str
    title: str
    description: str
    canonical: str
    robots: str
    indexable: bool
    noindex_reason: str
    language: str
    og: dict[str, str]
    twitter: dict[str, str]
    image: str
    alternates: tuple[tuple[str, str], ...]
    schema: tuple[dict[str, Any], ...]
    breadcrumbs: tuple[tuple[str, str], ...]
    provenance: dict[str, str]
    policy_version: str = POLICY_VERSION
    warnings: tuple[str, ...] = ()
    object_ref: tuple[str, str] | None = None

    def as_report(self) -> dict[str, Any]:
        return {
            "page_type": self.page_type,
            "title": self.title,
            "description": self.description,
            "canonical": self.canonical,
            "robots": self.robots,
            "indexable": self.indexable,
            "noindex_reason": self.noindex_reason,
            "language": self.language,
            "image": self.image,
            "alternates": [list(pair) for pair in self.alternates],
            "schema_types": [str(node.get("@type", "")) for node in self.schema],
            "provenance": self.provenance,
            "policy_version": self.policy_version,
            "warnings": list(self.warnings),
        }
