"""Entity graph kept in ``SeoEntity``/``SeoEntityEdge`` from real relations only.

Apps register a graph source in their ``seo_providers`` module; each yields nodes and
edges derived from foreign keys (brand -> model -> chipset -> tool -> guide -> post).
Nothing is inferred, so every edge is backed by a database relation.
"""

from __future__ import annotations

from django.db import transaction
from django.utils.text import slugify

from .contentsources import GraphData, graph_sources
from .models import SeoEntity, SeoEntityEdge


def _slug(key: str) -> str:
    return slugify(key.replace(":", "-"))[:255]


@transaction.atomic
def rebuild() -> dict[str, int]:
    """Upsert nodes and edges from every source; retire what no longer exists."""
    nodes: dict[str, tuple[str, str, str]] = {}
    edges: set[tuple[str, str, str]] = set()
    for source in graph_sources():
        data: GraphData = source()
        for node in data.nodes:
            nodes[node.key] = (node.name, node.entity_type, node.url)
        edges.update((edge.source, edge.target, edge.relation) for edge in data.edges)
    entities: dict[str, SeoEntity] = {}
    for key, (name, entity_type, url) in nodes.items():
        entity, _created = SeoEntity.objects.update_or_create(
            slug=_slug(key),
            defaults={
                "name": name[:255],
                "entity_type": entity_type,
                "attributes": {"url": url, "key": key},
                "is_active": True,
            },
        )
        entities[key] = entity
    SeoEntity.objects.filter(is_active=True).exclude(
        slug__in=[entity.slug for entity in entities.values()]
    ).update(is_active=False)
    live_edges = 0
    keep: list[int] = []
    for source_key, target_key, relation in sorted(edges):
        source_entity, target_entity = (
            entities.get(source_key),
            entities.get(target_key),
        )
        if source_entity is None or target_entity is None:
            continue
        edge, _created = SeoEntityEdge.objects.update_or_create(
            source=source_entity,
            target=target_entity,
            relation_type=relation,
            defaults={"weight": 1.0},
        )
        keep.append(edge.pk)
        live_edges += 1
    SeoEntityEdge.objects.exclude(pk__in=keep).delete()
    return {"entities": len(entities), "edges": live_edges}


def neighbors(slug: str) -> list[SeoEntity]:
    """Entities directly related to ``slug`` in either direction."""
    entity = SeoEntity.objects.filter(slug=slug, is_active=True).first()
    if entity is None:
        return []
    ids = set(
        SeoEntityEdge.objects.filter(source=entity).values_list("target_id", flat=True)
    ) | set(
        SeoEntityEdge.objects.filter(target=entity).values_list("source_id", flat=True)
    )
    return list(SeoEntity.objects.filter(pk__in=ids, is_active=True).order_by("name"))
