"""HTTP surface of the central search: results page and suggestion partial."""

from __future__ import annotations

import re
from typing import Final

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_headers

from .engine import HitGroup, search, search_grouped
from .popular import popular_blocks_data
from .registry import get_provider, get_providers
from .suggest import FEW_RESULTS, Suggestion, did_you_mean
from .text import clean_query
from .types import SearchHit

DEFAULT_SUGGEST_LIMIT: Final[int] = 8
MAX_SUGGEST_LIMIT: Final[int] = 20
PAGE_GROUP_SIZE: Final[int] = 10
PAGE_SINGLE_KIND_SIZE: Final[int] = 50
FILTER_PREFIX: Final[str] = "filter."
MAX_FILTER_VALUE_LENGTH: Final[int] = 64
MAX_HEADING_LENGTH: Final[int] = 80

_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,159}$")
_DOM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_FILTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def parse_kinds(request: HttpRequest) -> tuple[str, ...]:
    """Kinds from repeated or comma separated ``kinds`` parameters, validated."""
    kinds: dict[str, None] = {}
    for raw in request.GET.getlist("kinds"):
        for part in raw.split(","):
            name = part.strip().lower()
            if _KIND.match(name):
                kinds.setdefault(name)
    return tuple(kinds)


def parse_filters(request: HttpRequest) -> dict[str, str]:
    """``filter.<name>=<value>`` parameters handed to the providers."""
    filters: dict[str, str] = {}
    for key in request.GET:
        if not key.startswith(FILTER_PREFIX):
            continue
        name = key[len(FILTER_PREFIX) :]
        value = request.GET.get(key, "")
        if _FILTER_NAME.match(name) and value:
            filters[name] = value[:MAX_FILTER_VALUE_LENGTH]
    return filters


def _bounded(raw: str | None, default: int, high: int) -> int:
    try:
        return max(1, min(int(raw or default), high))
    except ValueError:
        return default


def _rows_and_groups(hits: list[SearchHit]) -> tuple[list[dict], list[dict]]:
    """Option rows (with a running index for ids) and the same rows grouped by kind."""
    rows: list[dict] = []
    groups: dict[str, dict] = {}
    for index, hit in enumerate(hits):
        provider = get_provider(hit.kind)
        row = {
            "hit": hit,
            "index": index,
            "kind": provider.label if provider else hit.kind,
        }
        rows.append(row)
        group = groups.setdefault(
            hit.kind,
            {
                "kind": hit.kind,
                "label": provider.label_plural if provider else hit.kind,
                "icon": provider.icon if provider else "search",
                "rows": [],
            },
        )
        group["rows"].append(row)
    return rows, list(groups.values())


@never_cache
@require_GET
def suggest(request: HttpRequest) -> HttpResponse:
    """Accessible listbox options for the combobox component (per keystroke).

    ``q`` query; ``kinds`` restricts providers (pick-only kinds allowed); ``mode``
    is ``navigate`` (options are links, grouped by kind with an icon) or ``pick``
    (options carry a value); ``id`` prefixes the option ids so several comboboxes
    can share a page; ``filter.<name>`` narrows providers (for example
    ``filter.brand``). Navigation lists add "Did you mean ...?" links when few
    results were found.
    """
    query = clean_query(request.GET.get("q"))
    kinds = parse_kinds(request)
    mode = "pick" if request.GET.get("mode") == "pick" else "navigate"
    prefix = request.GET.get("id", "")
    hits = search(
        query,
        kinds=kinds or None,
        limit=_bounded(
            request.GET.get("limit"), DEFAULT_SUGGEST_LIMIT, MAX_SUGGEST_LIMIT
        ),
        user=request.user,
        filters=parse_filters(request),
    )
    rows, groups = _rows_and_groups(hits)
    suggestions: list[Suggestion] = []
    if mode == "navigate" and query and len(hits) < FEW_RESULTS:
        suggestions = did_you_mean(
            query,
            user=request.user,
            kinds=kinds or None,
            exclude_urls=[hit.url for hit in hits],
        )
    return render(
        request,
        "search/suggest.html",
        {
            "rows": rows,
            "groups": groups if mode == "navigate" else [],
            "suggestions": suggestions,
            "query": query,
            "mode": mode,
            "prefix": prefix if _DOM_ID.match(prefix) else "combobox",
            "kinds": ",".join(kinds),
            "show_kind": False,
        },
    )


def _result_filters(user: object) -> list[dict[str, str]]:
    return [
        {"kind": provider.kind, "label": provider.label_plural}
        for provider in get_providers(user)
    ]


def _slug_param(request: HttpRequest, name: str) -> str:
    value = request.GET.get(name, "")
    return value if _SLUG.match(value) else ""


@require_GET
def popular(request: HttpRequest) -> HttpResponse:
    """Popular blocks fragment (loaded lazily by ``{% popular_blocks %}``).

    ``kinds`` (provider kinds), ``brand`` / ``chipset`` (slugs), ``heading`` (at
    most 80 characters, escaped) and ``layout`` (``row`` or ``stack``).
    """
    kinds = ",".join(parse_kinds(request))
    scope = {
        "brand": _slug_param(request, "brand"),
        "chipset": _slug_param(request, "chipset"),
    }
    return render(
        request,
        "search/popular.html",
        {
            "blocks": popular_blocks_data(kinds, scope),
            "heading": request.GET.get("heading", "")[:MAX_HEADING_LENGTH],
            "stack": request.GET.get("layout") == "stack",
        },
    )


@require_GET
@vary_on_headers("HX-Request")
def results(request: HttpRequest) -> HttpResponse:
    """Full results page grouped by kind, filterable with ``kinds``."""
    query = clean_query(request.GET.get("q"))
    visible = {provider.kind for provider in get_providers(request.user)}
    selected = tuple(kind for kind in parse_kinds(request) if kind in visible)
    groups: list[HitGroup] = search_grouped(
        query,
        kinds=selected or None,
        per_kind=PAGE_SINGLE_KIND_SIZE if len(selected) == 1 else PAGE_GROUP_SIZE,
        user=request.user,
    )
    total = sum(len(group.hits) for group in groups)
    suggestions: list[Suggestion] = []
    if query and total < FEW_RESULTS:
        shown = [hit.url for group in groups for hit in group.hits]
        suggestions = did_you_mean(
            query, user=request.user, kinds=selected or None, exclude_urls=shown
        )
    context = {
        "query": query,
        "groups": groups,
        "total": total,
        "did_you_mean": suggestions,
        "kind_filters": _result_filters(request.user),
        "selected": selected,
    }
    template = (
        "search/_results.html"
        if request.headers.get("HX-Request")
        else "search/results.html"
    )
    return render(request, template, context)
