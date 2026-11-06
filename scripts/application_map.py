#!/usr/bin/env python3
"""Generate a read-only, evidence-anchored map of the Django application."""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import django
from django.apps import apps
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

ROOT = Path(__file__).resolve().parents[1]
INTERACTION_ATTRIBUTES = {
    name
    for action in ("get", "post", "put", "patch", "delete")
    for name in (f"hx-{action}",)
} | {
    "hx-trigger",
    "hx-target",
    "hx-swap",
    "hx-sync",
    "hx-boost",
    "hx-push-url",
    "hx-confirm",
}
TEMPLATE_DIRECTIVE = re.compile(
    r"{%\s*(?P<kind>extends|include)\s+[\"'](?P<template>[^\"']+)[\"']"
)
STATIC_REFERENCE = re.compile(
    r"(?:\{%\s*static\s+[\"'](?P<static_path>[^\"']+)[\"']\s*%\}"
    r"|\b(?:href|src)=[\"'](?P<path>[^\"']+)[\"'])"
)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _local_source(callback: Any, root: Path) -> str | None:
    try:
        source = inspect.getsourcefile(callback)
    except (TypeError, OSError):
        return None
    if source is None:
        return None
    source_path = Path(source)
    if not source_path.is_file():
        return None
    try:
        return _relative_path(source_path, root)
    except OSError:
        return None


def _patterns(patterns: list[Any], root: Path, prefix: str = "", namespace: str = ""):
    for item in patterns:
        route = getattr(item.pattern, "_route", str(item.pattern))
        full_pattern = f"{prefix}{route}"
        if isinstance(item, URLResolver):
            child_namespace = item.namespace or namespace
            yield from _patterns(item.url_patterns, root, full_pattern, child_namespace)
            continue
        if not isinstance(item, URLPattern):
            continue

        callback = item.callback
        name = f"{namespace}:{item.name}" if namespace and item.name else item.name
        yield {
            "name": name,
            "pattern": full_pattern,
            "view": (
                f"{callback.__module__}.{callback.__qualname__}"
                if hasattr(callback, "__qualname__")
                else str(callback)
            ),
            "source": _local_source(callback, root),
        }


class _TemplateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.interactions: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        line, _ = self.getpos()
        htmx = {
            name: values[name]
            for name in sorted(INTERACTION_ATTRIBUTES)
            if name in values
        }
        alpine = {
            name: value for name, value in attrs if name.startswith(("x-", "@", ":"))
        }
        if not htmx and not alpine:
            return
        self.interactions.append(
            {
                "line": line,
                "tag": tag,
                "htmx": htmx,
                "alpine": alpine,
            }
        )


def _template_asset_references(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    refs: list[str] = []
    for match in STATIC_REFERENCE.finditer(text):
        value = match.group("static_path") or match.group("path")
        if not value:
            continue
        if value.startswith("/"):
            value = value.lstrip("/")
        if "." in value:
            refs.append(value)
    return sorted(dict.fromkeys(refs))


def _scan_template(path: Path, root: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    parser = _TemplateParser()
    parser.feed(source)
    directives = [
        {"kind": match.group("kind"), "template": match.group("template")}
        for match in TEMPLATE_DIRECTIVE.finditer(source)
    ]
    return {
        "path": _relative_path(path, root),
        "extends_and_includes": directives,
        "interactions": parser.interactions,
        "asset_references": _template_asset_references(path),
    }


def _installed_local_apps(root: Path) -> list[dict[str, Any]]:
    app_root = (root / "apps").resolve()
    result: list[dict[str, Any]] = []
    for config in apps.get_app_configs():
        app_path = Path(config.path).resolve()
        try:
            relative = app_path.relative_to(app_root)
        except ValueError:
            continue

        models = []
        for model in config.get_models():
            fields = []
            for field in model._meta.get_fields():
                if not getattr(field, "concrete", False):
                    continue
                remote = getattr(getattr(field, "remote_field", None), "model", None)
                fields.append(
                    {
                        "name": field.name,
                        "type": field.get_internal_type(),
                        "related_model": (
                            f"{remote._meta.app_label}.{remote._meta.model_name}"
                            if remote is not None
                            else None
                        ),
                    }
                )
            models.append(
                {
                    "name": model.__name__,
                    "fields": fields,
                    "constraints": [
                        {
                            "name": getattr(constraint, "name", ""),
                            "type": type(constraint).__name__,
                        }
                        for constraint in model._meta.constraints
                    ],
                }
            )

        backend_tests = root / "tests" / "backend" / relative.name
        result.append(
            {
                "label": config.label,
                "package": config.name,
                "path": _relative_path(app_path, root),
                "models": models,
                "backend_test_files": (
                    [
                        _relative_path(test, root)
                        for test in sorted(backend_tests.rglob("test_*.py"))
                    ]
                    if backend_tests.is_dir()
                    else []
                ),
            }
        )
    return sorted(result, key=lambda app: app["label"])


def _template_roots(root: Path) -> list[Path]:
    candidates = [root / "templates"]
    app_root = root / "apps"
    if app_root.is_dir():
        candidates.extend(app_root.glob("*/templates"))
    return sorted(
        {path.resolve() for path in candidates if path.is_dir()},
        key=lambda path: path.as_posix(),
    )


def _frontend_sources(root: Path) -> dict[str, list[str]]:
    extensions = {
        "javascript": {".js", ".mjs"},
        "stylesheets": {".css", ".scss"},
    }
    roots = [root / "static", *sorted((root / "apps").glob("*/static"))]
    result: dict[str, list[str]] = {}
    for label, suffixes in extensions.items():
        found = {
            _relative_path(path, root)
            for directory in roots
            if directory.is_dir()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        }
        result[label] = sorted(found)
    return result


def _admin_template_paths(root: Path) -> list[str]:
    admin_root = root / "templates" / "admin"
    if not admin_root.is_dir():
        return []
    return sorted(
        _relative_path(path, root)
        for path in admin_root.rglob("*.html")
        if path.is_file()
    )


def _admin_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = []
    for route in routes:
        name = route.get("name") or ""
        pattern = route.get("pattern") or ""
        if "admin" in pattern.lower() or name.startswith("admin"):
            matches.append(route)
    return sorted(
        matches, key=lambda route: (route.get("name") or "", route.get("pattern") or "")
    )


def build_application_map(root: Path = ROOT) -> dict[str, Any]:
    """Return the static inventory of apps, routes, templates and test files."""
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        os.environ["DJANGO_SETTINGS_MODULE"] = "app.settings.test"
    if not apps.ready:
        django.setup()

    template_files = sorted(
        {
            path.resolve()
            for directory in _template_roots(root)
            for path in directory.rglob("*.html")
            if path.is_file()
        },
        key=lambda path: path.as_posix(),
    )
    routes = list(_patterns(get_resolver().url_patterns, root))
    route_inventory = sorted(
        routes,
        key=lambda route: (
            route["name"] or "",
            route["pattern"],
            route["view"],
        ),
    )
    admin_routes = _admin_routes(route_inventory)
    templates = [_scan_template(path, root) for path in template_files]
    return {
        "schema_version": 1,
        "interpretation": (
            "Static source inventory and candidate relationships only; it is not "
            "a product specification or proof of runtime behavior."
        ),
        "settings_module": settings.SETTINGS_MODULE,
        "apps": _installed_local_apps(root),
        "routes": route_inventory,
        "admin_routes": admin_routes,
        "admin_templates": _admin_template_paths(root),
        "templates": templates,
        "template_asset_usage": [
            {
                "path": template["path"],
                "asset_references": template["asset_references"],
            }
            for template in templates
            if template["asset_references"]
        ],
        "frontend_sources": _frontend_sources(root),
        "frontend_asset_inventory": {
            "public": [
                asset
                for asset in _frontend_sources(root)["javascript"]
                + _frontend_sources(root)["stylesheets"]
                if (
                    "/admin/" not in asset.lower()
                    and not asset.lower().endswith("admin.js")
                    and not asset.lower().endswith("admin.css")
                    and not asset.lower().endswith("tokens.css")
                )
            ],
            "admin": [
                asset
                for asset in _frontend_sources(root)["javascript"]
                + _frontend_sources(root)["stylesheets"]
                if "/admin/" in asset.lower()
                or asset.lower().endswith("admin.js")
                or asset.lower().endswith("admin.css")
            ],
            "shared": [
                asset
                for asset in _frontend_sources(root)["javascript"]
                + _frontend_sources(root)["stylesheets"]
                if asset.lower().endswith("tokens.css")
            ],
        },
        "integration_test_files": sorted(
            _relative_path(path, root)
            for path in (root / "tests" / "backend" / "integrations").glob("test_*.py")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Write the application map as JSON to a file or standard output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSON to this path (default: standard output)",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        inventory = build_application_map(args.root.resolve())
        serialized = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"application-map: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
