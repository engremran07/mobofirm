"""Interlinking: proposals built from real relations, reviewed before anything renders.

Candidates come only from relations that exist in the database and are already in the
entity graph (brand has model, model uses chipset, tool supports chipset or brand, post
covers brand or model) plus two derived hops (model to the tools of its chipset, chipset
to its flashing-guide post). Every proposal records the relation that justifies it, an
anchor chosen for variety, and waits as ``proposed`` until a reviewer accepts it. A link
budget per source keeps hubs from diluting, rejected pairs are never proposed again,
and a source never links to itself or to the same target twice.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from django.apps import apps as django_apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import entity_graph
from .models import (
    LinkableEntity,
    LinkSuggestion,
    PageObservation,
    PageSnapshot,
    SeoEntity,
    SeoEntityEdge,
)
from .services.internal_linking.engine import _crawl_budget_limit, _is_excluded

NODE_LABELS = {
    "brand": "firmwares.brand",
    "chipset": "firmwares.chipset",
    "model": "firmwares.model",
    "tool": "firmwares.flashingtool",
    "post": "blog.post",
}
RELATION_LABELS = {
    "brand_to_model": "Brand lists its model",
    "model_to_brand": "Model names its brand",
    "model_to_chipset": "Model links its chipset",
    "chipset_to_model": "Chipset lists a device that uses it",
    "tool_to_chipset": "Tool links a chipset it flashes",
    "chipset_to_tool": "Chipset links a tool that supports it",
    "tool_to_brand": "Tool links a brand it supports",
    "brand_to_tool": "Brand links a tool that supports it",
    "post_to_brand": "Post links the brand it covers",
    "brand_to_post": "Brand links a post about it",
    "post_to_model": "Post links the model it covers",
    "model_to_post": "Model links a guide about it",
    "model_to_tool": "Model links a tool for its chipset",
    "chipset_to_guide": "Chipset links its flashing guide",
    "guide_to_chipset": "Guide links its chipset",
}
DIRECT, DERIVED = 1.0, 0.8
BUDGET_REQUEST = 10
EXTERNAL_REL = ("nofollow", "noopener", "noreferrer")
_ANCHOR_TAG = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([a-zA-Z_:][-\w:.]*)\s*=\s*("[^"]*"|'[^']*')""")
_REL_ATTR = re.compile(r"""\srel\s*=\s*("[^"]*"|'[^']*')""", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    source_key: str
    target_key: str
    relation: str
    score: float
    reason: str


@dataclass
class ProposalReport:
    created: int = 0
    updated: int = 0
    retired: int = 0
    skipped_rejected: int = 0
    skipped_budget: int = 0
    entities: int = 0
    per_relation: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _anchor_variants(name: str, kind: str) -> list[str]:
    variants = {
        "model": [name, f"{name} firmware", f"{name} firmware download"],
        "chipset": [name, f"{name} chipset", f"{name} firmware and tools"],
        "brand": [name, f"{name} firmware", f"{name} devices"],
        "tool": [name, f"{name} flashing tool", f"flash with {name}"],
        "post": [name],
    }
    return variants.get(kind, [name])


def _model_available(label: str) -> bool:
    try:
        django_apps.get_model(label)
    except LookupError:
        return False
    return True


def _entity_pk(key: str) -> tuple[str, str]:
    kind, _, pk = key.partition(":")
    return kind, pk


def sync_linkables() -> dict[str, LinkableEntity]:
    """One ``LinkableEntity`` per active graph node whose model is installed."""
    entity_graph.rebuild()
    linkables: dict[str, LinkableEntity] = {}
    for node in SeoEntity.objects.filter(is_active=True):
        key = str(node.attributes.get("key", ""))
        kind, pk = _entity_pk(key)
        label = NODE_LABELS.get(kind)
        url = str(node.attributes.get("url", ""))
        if not label or not pk or not url or not _model_available(label):
            continue
        app_label, _, model = label.partition(".")
        content_type = ContentType.objects.get(app_label=app_label, model=model)
        linkable, _created = LinkableEntity.objects.update_or_create(
            content_type=content_type,
            object_id=pk,
            defaults={
                "title": node.name[:255],
                "url": url[:200],
                "entity_type": kind,
                "keywords": [node.name],
                "is_active": True,
            },
        )
        linkables[key] = linkable
    return linkables


def _guide_pairs() -> list[tuple[str, str]]:
    """(chipset key, post key) for chipset flashing-guide posts that are live."""
    if not (
        _model_available("firmwares.AutomatedBlogLink")
        and _model_available("blog.Post")
    ):
        return []
    link_model = django_apps.get_model("firmwares", "AutomatedBlogLink")
    post_model = django_apps.get_model("blog", "Post")
    live = set(post_model.objects.live().values_list("pk", flat=True))
    return [
        (f"chipset:{row.chipset_id}", f"post:{row.post_id}")
        for row in link_model.objects.filter(kind="chipset", chipset__isnull=False)
        if row.post_id in live
    ]


def candidates() -> list[Candidate]:
    found: list[Candidate] = []
    chipset_tools: dict[str, list[str]] = defaultdict(list)
    model_chipsets: dict[str, list[str]] = defaultdict(list)

    def add(source: str, target: str, relation: str, score: float, reason: str) -> None:
        found.append(Candidate(source, target, relation, score, reason))

    for edge in SeoEntityEdge.objects.select_related("source", "target"):
        source = str(edge.source.attributes.get("key", ""))
        target = str(edge.target.attributes.get("key", ""))
        relation = edge.relation_type
        if relation == "has_model":
            why = "The model belongs to the brand."
            add(source, target, "brand_to_model", DIRECT, why)
            add(target, source, "model_to_brand", DIRECT, why)
        elif relation == "uses_chipset":
            model_chipsets[source].append(target)
            add(
                source,
                target,
                "model_to_chipset",
                DIRECT,
                "The model uses this chipset.",
            )
            add(
                target,
                source,
                "chipset_to_model",
                DERIVED,
                "The device uses this chipset.",
            )
        elif relation == "supports_chipset":
            chipset_tools[target].append(source)
            why = "The tool supports this chipset."
            add(source, target, "tool_to_chipset", DIRECT, why)
            add(target, source, "chipset_to_tool", DIRECT, why)
        elif relation == "supports_brand":
            why = "The tool supports this brand."
            add(source, target, "tool_to_brand", DIRECT, why)
            add(target, source, "brand_to_tool", DERIVED, why)
        elif relation == "covers_brand":
            why = "The post covers this brand."
            add(source, target, "post_to_brand", DIRECT, why)
            add(target, source, "brand_to_post", DERIVED, why)
        elif relation == "covers_model":
            add(source, target, "post_to_model", DIRECT, "The post covers this model.")
            add(
                target,
                source,
                "model_to_post",
                DIRECT,
                "The post is a guide for this model.",
            )
    for model_key, chipsets in model_chipsets.items():
        for chipset_key in chipsets:
            for tool_key in chipset_tools.get(chipset_key, []):
                why = "The tool supports the chipset this model uses."
                add(model_key, tool_key, "model_to_tool", DERIVED, why)
    for chipset_key, post_key in _guide_pairs():
        add(
            chipset_key,
            post_key,
            "chipset_to_guide",
            DIRECT,
            "The chipset's flashing guide.",
        )
        add(
            post_key,
            chipset_key,
            "guide_to_chipset",
            DIRECT,
            "The guide is about this chipset.",
        )
    return found


def _budget_used(source: LinkableEntity) -> int:
    return LinkSuggestion.objects.filter(
        source=source, is_active=True, status__in=["proposed", "accepted"]
    ).count()


def _inbound(target: LinkableEntity) -> int:
    return LinkSuggestion.objects.filter(
        target=target, status__in=["proposed", "accepted"]
    ).count()


@transaction.atomic
def generate_proposals() -> ProposalReport:
    """Create review proposals for every justified link; never touch decided ones."""
    report = ProposalReport()
    linkables = sync_linkables()
    report.entities = len(linkables)
    by_source: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates():
        known = candidate.source_key in linkables and candidate.target_key in linkables
        if known and candidate.source_key != candidate.target_key:
            by_source[candidate.source_key].append(candidate)
    seen: set[tuple[int, int]] = set()
    for source_key, options in by_source.items():
        source = linkables[source_key]
        limit = _crawl_budget_limit(source, BUDGET_REQUEST)
        options.sort(
            key=lambda c: (
                -c.score,
                _inbound(linkables[c.target_key]),
                linkables[c.target_key].title,
            )
        )
        for option in options:
            target = linkables[option.target_key]
            existing = LinkSuggestion.objects.filter(
                source=source, target=target
            ).first()
            if (
                existing is not None
                and existing.status == LinkSuggestion.Status.REJECTED
            ):
                report.skipped_rejected += 1
                continue
            if existing is None and _budget_used(source) >= limit:
                report.skipped_budget += 1
                continue
            variants = _anchor_variants(target.title, target.entity_type)
            anchor = variants[_inbound(target) % len(variants)]
            if _is_excluded(source.url, target.url, anchor):
                continue
            if existing is None:
                LinkSuggestion.objects.create(
                    source=source,
                    target=target,
                    score=option.score,
                    relation=option.relation,
                    anchor=anchor[:160],
                    reason=option.reason,
                )
                report.created += 1
                report.per_relation[option.relation] += 1
            elif existing.status == LinkSuggestion.Status.PROPOSED:
                existing.score = option.score
                existing.relation = option.relation
                existing.reason = option.reason
                existing.is_active = True
                existing.save()
                report.updated += 1
            seen.add((source.pk, target.pk))
    stale = LinkSuggestion.objects.filter(status=LinkSuggestion.Status.PROPOSED)
    for suggestion in stale.exclude(relation=""):
        if (suggestion.source_id, suggestion.target_id) not in seen:
            suggestion.delete()
            report.retired += 1
    return report


class DecisionError(Exception):
    """A proposal cannot be decided in its current state."""


def decide(suggestion: LinkSuggestion, decision: str, user: Any) -> LinkSuggestion:
    """accept renders the link; reject keeps it from being proposed again; reset reopens."""
    if decision == "accept":
        suggestion.status = LinkSuggestion.Status.ACCEPTED
        suggestion.is_applied = True
        suggestion.is_active = True
    elif decision == "reject":
        suggestion.status = LinkSuggestion.Status.REJECTED
        suggestion.is_applied = False
    elif decision == "reset":
        suggestion.status = LinkSuggestion.Status.PROPOSED
        suggestion.is_applied = False
        suggestion.is_active = True
    else:
        raise DecisionError(f"Unknown decision {decision!r}.")
    suggestion.decided_by = user if getattr(user, "is_authenticated", False) else None
    suggestion.decided_at = timezone.now()
    suggestion.save()
    return suggestion


def decide_many(ids: list[int], decision: str, user: Any) -> int:
    if decision not in {"accept", "reject", "reset"}:
        raise DecisionError(f"Unknown decision {decision!r}.")
    changed = 0
    for suggestion in LinkSuggestion.objects.filter(pk__in=ids):
        decide(suggestion, decision, user)
        changed += 1
    return changed


def accepted_links(obj: Any, limit: int = 8) -> list[LinkSuggestion]:
    """Accepted links out of ``obj``, best first (for related-link blocks)."""
    pk = getattr(obj, "pk", None)
    if pk is None:
        return []
    content_type = ContentType.objects.get_for_model(type(obj))
    return list(
        LinkSuggestion.objects.filter(
            source__content_type=content_type,
            source__object_id=str(pk),
            status=LinkSuggestion.Status.ACCEPTED,
            is_active=True,
            target__is_active=True,
        )
        .select_related("target")
        .order_by("-score", "target__title")[:limit]
    )


def apply_external_link_policy(html: str, site_host: str) -> str:
    """Add ``rel="nofollow noopener noreferrer"`` to links that leave the site."""

    def rewrite(match: re.Match[str]) -> str:
        attrs = match.group(1)
        found = {name.lower(): value[1:-1] for name, value in _ATTR.findall(attrs)}
        parts = urlsplit(found.get("href", ""))
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return match.group(0)
        if parts.netloc.lower() == site_host.lower():
            return match.group(0)
        tokens = found.get("rel", "").split()
        merged = " ".join(dict.fromkeys([*tokens, *EXTERNAL_REL]))
        return f'<a{_REL_ATTR.sub("", attrs)} rel="{merged}">'

    return _ANCHOR_TAG.sub(rewrite, html)


def record_link_counts(run: Any) -> int:
    """Store measured inbound/outbound link counts from a render crawl."""
    outbound: dict[str, int] = {}
    inbound: dict[str, int] = defaultdict(int)
    for observation in PageObservation.objects.filter(run=run).only(
        "url", "internal_links"
    ):
        path = urlsplit(observation.url).path or "/"
        links = {urlsplit(link).path or "/" for link in observation.internal_links}
        links.discard(path)
        outbound[path] = len(links)
        for link in links:
            inbound[link] += 1
    updated = 0
    paths = set(outbound) | set(inbound)
    for snapshot in PageSnapshot.objects.filter(path__in=list(paths)):
        if snapshot.path in outbound:
            snapshot.links_out = outbound[snapshot.path]
        snapshot.links_in = inbound.get(snapshot.path, 0)
        snapshot.save(update_fields=["links_out", "links_in", "updated_at"])
        updated += 1
    return updated


def status_counts() -> dict[str, int]:
    rows = LinkSuggestion.objects.exclude(relation="").aggregate(
        proposed=Count("pk", filter=Q(status="proposed", is_active=True)),
        accepted=Count("pk", filter=Q(status="accepted")),
        rejected=Count("pk", filter=Q(status="rejected")),
    )
    return {key: int(value or 0) for key, value in rows.items()}
