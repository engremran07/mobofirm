"""Enumerate named URL routes with sample kwargs for smoke tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from django.urls import URLPattern, URLResolver

CONVERTER_SAMPLES: dict[str, str | int | float] = {
    "UUIDConverter": str(uuid.UUID("00000000-0000-4000-8000-000000000001")),
    "IntConverter": 1,
    "PathConverter": "smoke-test/path",
    "SlugConverter": "smoke-test",
    "StringConverter": "smoke-test",
    "UnicodeConverter": "smoke-test",
    "DateConverter": "2026-01-01",
    "FloatConverter": 1.0,
    "FormatSuffixConverter": "json",
}
REGEX_GROUP_SAMPLES = {
    "app_label": "auth",
    "format": "json",
    "key": "smoke-test",
    "pk": "1",
    "uidb36": "1",
}


def _sample_kwargs(
    pattern: URLPattern, route_name: str
) -> dict[str, str | int | float]:
    kwargs: dict[str, str | int | float] = {}
    for name, converter in pattern.pattern.converters.items():
        converter_name = converter.__class__.__name__
        if converter_name not in CONVERTER_SAMPLES:
            raise AssertionError(
                f"No smoke-test value is configured for {converter_name} "
                f"in route {route_name}"
            )
        kwargs[name] = CONVERTER_SAMPLES[converter_name]
    for group_name in pattern.pattern.regex.groupindex:
        if group_name in kwargs:
            continue
        if group_name not in REGEX_GROUP_SAMPLES:
            raise AssertionError(
                f"No smoke-test value is configured for regex group {group_name} "
                f"in route {route_name}"
            )
        kwargs[group_name] = REGEX_GROUP_SAMPLES[group_name]
    return kwargs


def named_routes(
    patterns: Any, namespace: tuple[str, ...] = ()
) -> Iterator[tuple[str, dict[str, str | int | float]]]:
    """Yield ``(namespaced route name, sample kwargs)`` for every named URL."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            child_namespace = namespace
            if pattern.namespace:
                child_namespace += (pattern.namespace,)
            yield from named_routes(pattern.url_patterns, child_namespace)
            continue

        if not isinstance(pattern, URLPattern) or not pattern.name:
            continue
        route_name = ":".join((*namespace, pattern.name))
        yield route_name, _sample_kwargs(pattern, route_name)
