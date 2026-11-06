"""Registration seam for content that changes public URLs.

An app's ``seo_providers`` module registers each public model once; ``apps.seo``
then observes saves and deletes after commit without importing the model itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LinkableInfo:
    title: str
    url: str
    keywords: str = ""


@dataclass(frozen=True)
class ContentSource:
    label: str
    url: Callable[[Any], str]
    is_public: Callable[[Any], bool]
    linkable: Callable[[Any], LinkableInfo | None] | None = None
    # Content edits (not just publish/unpublish/move) are worth announcing.
    notify_on_edit: bool = False


_sources: dict[str, ContentSource] = {}
_graph_sources: list[Callable[[], GraphData]] = []


@dataclass(frozen=True)
class GraphNode:
    key: str
    name: str
    entity_type: str
    url: str = ""


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str


@dataclass(frozen=True)
class GraphData:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


def register_graph_source(source: Callable[[], GraphData]) -> None:
    """Apps contribute real entities and relations; ``apps.seo`` stores the graph."""
    if source not in _graph_sources:
        _graph_sources.append(source)


def graph_sources() -> list[Callable[[], GraphData]]:
    return list(_graph_sources)


def register_content_source(
    label: str,
    *,
    url: Callable[[Any], str],
    is_public: Callable[[Any], bool],
    linkable: Callable[[Any], LinkableInfo | None] | None = None,
    notify_on_edit: bool = False,
) -> None:
    _sources[label.lower()] = ContentSource(
        label.lower(), url, is_public, linkable, notify_on_edit
    )


def content_sources() -> dict[str, ContentSource]:
    return dict(_sources)
