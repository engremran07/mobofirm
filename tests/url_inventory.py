from __future__ import annotations

import uuid

from django.urls import URLPattern, URLResolver


def named_routes(patterns, namespace: tuple[str, ...] = ()):
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
        kwargs = {}
        for name, converter in pattern.pattern.converters.items():
            converter_name = converter.__class__.__name__
            if converter_name == "UUIDConverter":
                kwargs[name] = str(uuid.UUID("00000000-0000-4000-8000-000000000001"))
            elif converter_name == "IntConverter":
                kwargs[name] = 1
            elif converter_name == "PathConverter":
                kwargs[name] = "smoke-test/path"
            elif converter_name == "SlugConverter":
                kwargs[name] = "smoke-test"
            elif converter_name in {"StringConverter", "UnicodeConverter"}:
                kwargs[name] = "smoke-test"
            elif converter_name == "DateConverter":
                kwargs[name] = "2026-01-01"
            elif converter_name == "FloatConverter":
                kwargs[name] = 1.0
            elif converter_name == "FormatSuffixConverter":
                kwargs[name] = "json"
            else:
                raise AssertionError(
                    f"No smoke-test value is configured for {converter_name} "
                    f"in route {route_name}"
                )
        regex_group_samples = {
            "app_label": "auth",
            "format": "json",
            "key": "smoke-test",
            "pk": "1",
            "uidb36": "1",
        }
        for group_name in pattern.pattern.regex.groupindex:
            if group_name in kwargs:
                continue
            if group_name not in regex_group_samples:
                raise AssertionError(
                    f"No smoke-test value is configured for regex group {group_name} "
                    f"in route {route_name}"
                )
            kwargs[group_name] = regex_group_samples[group_name]
        yield route_name, kwargs
