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
from .registry import get_provider, get_providers
from .text import clean_query

DEFAULT_SUGGEST_LIMIT: Final[int] = 8
MAX_SUGGEST_LIMIT: Final[int] = 20
PAGE_GROUP_SIZE: Final[int] = 10
PAGE_SINGLE_KIND_SIZE: Final[int] = 50
FILTER_PREFIX: Final[str] = "filter."
MAX_FILTER_VALUE_LENGTH: Final[int] = 64

_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
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


@never_cache
@require_GET
def suggest(request: HttpRequest) -> HttpResponse:
    """Accessible listbox options for the combobox component (per keystroke).

    ``q`` query; ``kinds`` restricts providers (pick-only kinds allowed); ``mode``
    is ``navigate`` (options are links) or ``pick`` (options carry a value);
    ``id`` prefixes the option ids so several comboboxes can share a page;
    ``filter.<name>`` narrows providers (for example ``filter.brand``).
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
    rows = []
    for hit in hits:
        provider = get_provider(hit.kind)
        rows.append({"hit": hit, "kind": provider.label if provider else hit.kind})
    return render(
        request,
        "search/suggest.html",
        {
            "rows": rows,
            "query": query,
            "mode": mode,
            "prefix": prefix if _DOM_ID.match(prefix) else "combobox",
            "kinds": ",".join(kinds),
            "show_kind": mode == "navigate",
        },
    )


def _result_filters(user: object) -> list[dict[str, str]]:
    return [
        {"kind": provider.kind, "label": provider.label_plural}
        for provider in get_providers(user)
    ]


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
    context = {
        "query": query,
        "groups": groups,
        "total": sum(len(group.hits) for group in groups),
        "kind_filters": _result_filters(request.user),
        "selected": selected,
    }
    template = (
        "search/_results.html"
        if request.headers.get("HX-Request")
        else "search/results.html"
    )
    return render(request, template, context)
