"""Page inventory and inspector built from the real URL conf, sitemap registry and
resolver. Discovery never implies indexation: every URL is classified by policy."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.apps import apps as django_apps
from django.conf import settings
from django.test import RequestFactory
from django.urls import Resolver404, get_resolver, resolve
from django.urls.resolvers import RoutePattern, URLPattern, URLResolver

from .contracts import ResolvedSEO
from .managers import default_manager
from .models import Issue, PageObservation
from .policy import is_private_path
from .resolver import resolve as resolve_seo
from .rules import GROUP_BY_PAGE_TYPE, OBJECT_GROUPS
from .sitemap_registry import get_raw_sitemaps, get_sitemaps
from .siteurls import path_and_query, site_origin


def serving_host() -> str:
    origin_host = urlsplit(site_origin()).netloc
    if origin_host:
        return origin_host
    concrete = [h for h in settings.ALLOWED_HOSTS if h and not h.startswith((".", "*"))]
    return concrete[0] if concrete else "localhost"


def resolve_path(path: str) -> ResolvedSEO | None:
    """What the resolver intends for a site path, without an HTTP round trip."""
    try:
        match = resolve(urlsplit(path).path)
    except Resolver404:
        return None
    request = RequestFactory().get(path, HTTP_HOST=serving_host())
    request.resolver_match = match
    return resolve_seo(request, {})


@dataclass(frozen=True)
class InventoryItem:
    path: str
    source: str
    page_type: str
    indexable: bool
    reason: str
    canonical: str
    in_sitemap: bool
    conflicts: tuple[str, ...] = field(default_factory=tuple)


def iter_static_routes() -> Iterator[tuple[str, str]]:
    """(view name, path) for every parameterless ``path()`` route in the URL conf."""

    def walk(patterns, prefix: str, namespace: str) -> Iterator[tuple[str, str]]:
        for entry in patterns:
            if not isinstance(entry.pattern, RoutePattern):
                continue
            route = str(entry.pattern)
            if "<" in route:
                continue
            if isinstance(entry, URLResolver):
                child_ns = ":".join(p for p in (namespace, entry.namespace or "") if p)
                yield from walk(entry.url_patterns, prefix + route, child_ns)
            elif isinstance(entry, URLPattern) and entry.name:
                name = ":".join(p for p in (namespace, entry.name) if p)
                yield name, "/" + (prefix + route).lstrip("/")

    yield from walk(get_resolver().url_patterns, "", "")


def _locations(sitemaps: dict[str, type]) -> dict[str, str]:
    located: dict[str, str] = {}
    for section, sitemap_class in sitemaps.items():
        sitemap = sitemap_class()
        for item in sitemap.items():
            location = sitemap.location(item) if hasattr(sitemap, "location") else ""
            if location:
                located[urlsplit(str(location)).path or "/"] = section
    return located


def sitemap_paths() -> dict[str, str]:
    """Every sitemap location (path) mapped to its section name."""
    return _locations(get_sitemaps())


def raw_sitemap_paths() -> dict[str, str]:
    """Every public location of every section, before indexation filters."""
    return _locations(get_raw_sitemaps())


def object_title(label: str, pk: str) -> str:
    """Human label of a content object, or ``""`` when it no longer exists."""
    try:
        model = django_apps.get_model(label)
    except (LookupError, ValueError):
        return ""
    row = default_manager(model).filter(pk=pk).first()
    return str(row)[:255] if row is not None else ""


def describe(resolved: ResolvedSEO | None) -> dict[str, str]:
    """Review bucket and content object behind a resolved page."""
    where = {"group": "site", "object_label": "", "object_pk": "", "object_title": ""}
    if resolved is None:
        return where
    ref = resolved.object_ref
    where["group"] = GROUP_BY_PAGE_TYPE.get(resolved.page_type) or (
        OBJECT_GROUPS.get(ref[0], "site") if ref else "site"
    )
    if ref is not None:
        where.update(
            object_label=ref[0],
            object_pk=ref[1],
            object_title=object_title(ref[0], ref[1]),
        )
    return where


def locate(path: str) -> dict[str, str]:
    """Review bucket and content object behind a site path (no HTTP round trip)."""
    return describe(resolve_path(path))


def build_inventory() -> list[InventoryItem]:
    in_sitemap = sitemap_paths()
    entries: dict[str, str] = {}
    for _name, path in iter_static_routes():
        entries.setdefault(path, "urlconf")
    for path in in_sitemap:
        entries[path] = f"sitemap:{in_sitemap[path]}"
    items: list[InventoryItem] = []
    for path, source in sorted(entries.items()):
        resolved = resolve_path(path)
        if resolved is None:
            continue
        listed = path in in_sitemap
        conflicts: list[str] = []
        if listed and not resolved.indexable:
            conflicts.append("noindex_in_sitemap")
        if listed and urlsplit(resolved.canonical).path != path:
            conflicts.append("canonical_mismatch")
        if listed and is_private_path(path):
            conflicts.append("private_in_sitemap")
        items.append(
            InventoryItem(
                path=path,
                source=source,
                page_type=resolved.page_type,
                indexable=resolved.indexable,
                reason=resolved.noindex_reason,
                canonical=resolved.canonical,
                in_sitemap=listed,
                conflicts=tuple(conflicts),
            )
        )
    return items


def inspect_url(url: str) -> dict[str, object]:
    """Resolver intent, sitemap membership and the latest crawler observation."""
    path = path_and_query(url)
    resolved = resolve_path(path)
    latest = (
        PageObservation.objects.filter(url__endswith=path)
        .order_by("-observed_at")
        .first()
    )
    listed = urlsplit(path).path in sitemap_paths()
    issues = Issue.objects.filter(url__endswith=path, status=Issue.Status.OPEN)
    return {
        "path": path,
        "resolved": resolved,
        "in_sitemap": listed,
        "observation": latest,
        "issues": list(issues),
    }
