#!/usr/bin/env python3
"""Generate a focused feature-impact report for a changed Django source target."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.application_map import ROOT, build_application_map


def _normalise_target(root: Path, target: str) -> str:
    raw = target.strip().replace("\\", "/")
    if not raw:
        raise ValueError("Target must not be empty.")
    if raw.startswith(root.as_posix()):
        return Path(raw).resolve().relative_to(root.resolve()).as_posix()
    if raw.startswith("/"):
        return Path(raw).resolve().relative_to(root.resolve()).as_posix()
    return raw.strip("./")


def _owner_app_for_target(target: str, inventory: dict[str, Any]) -> str | None:
    for app in inventory["apps"]:
        label = app["label"]
        prefixes = [
            f"apps/{label}/",
            f"apps/{label}",
            f"{label}/",
            label,
        ]
        if any(
            target == prefix.rstrip("/") or target.startswith(prefix)
            for prefix in prefixes
        ):
            return label
    return None


def _match_routes_for_app(
    routes: list[dict[str, Any]], owner_app: str | None
) -> list[dict[str, Any]]:
    if owner_app is None:
        return []
    matches: list[dict[str, Any]] = []
    for route in routes:
        source = route.get("source") or ""
        view = route.get("view") or ""
        if source.startswith(f"apps/{owner_app}/") or f"{owner_app}." in view:
            matches.append(route)
    return sorted(
        matches, key=lambda route: (route.get("name") or "", route.get("pattern") or "")
    )


def _match_templates_for_app(
    templates: list[dict[str, Any]], owner_app: str | None
) -> list[str]:
    if owner_app is None:
        return []
    matches: list[str] = []
    for template in templates:
        path = template.get("path") or ""
        if path.startswith((f"apps/{owner_app}/templates/", f"templates/{owner_app}/")):
            matches.append(path)
    return sorted(set(matches))


def _match_tests_for_app(tests: list[str], owner_app: str | None) -> list[str]:
    if owner_app is None:
        return []
    return sorted(
        path for path in tests if path.startswith(f"tests/backend/{owner_app}/")
    )


def _match_frontend_assets(inventory: dict[str, Any], target: str) -> list[str]:
    all_assets = []
    for asset_group in inventory["frontend_sources"].values():
        all_assets.extend(asset_group)
    matches = []
    if "admin" in target.lower() or target.startswith("templates/admin/"):
        matches.extend(inventory["frontend_asset_inventory"]["admin"])
    if "site" in target.lower() or target.startswith("templates/"):
        matches.extend(inventory["frontend_asset_inventory"]["public"])
    matches.extend(
        asset
        for asset in all_assets
        if any(fragment in asset.lower() for fragment in ("site", "admin", "tokens"))
    )
    return sorted(dict.fromkeys(matches))


def build_feature_impact_report(target: str, root: Path = ROOT) -> dict[str, Any]:
    """Map ``target`` to its owning app, routes, templates, assets and tests."""
    inventory = build_application_map(root)
    normalised = _normalise_target(root, target)
    owner_app = _owner_app_for_target(normalised, inventory)

    routes = _match_routes_for_app(inventory["routes"], owner_app)
    templates = _match_templates_for_app(inventory["templates"], owner_app)
    if owner_app is None and normalised.startswith("templates/"):
        templates = [
            template["path"]
            for template in inventory["templates"]
            if template["path"] == normalised
        ]

    tests = _match_tests_for_app(
        [item for app in inventory["apps"] for item in app["backend_test_files"]],
        owner_app,
    )

    related_apps = {
        app["label"]
        for route in routes
        for app in inventory["apps"]
        if route.get("source", "").startswith(f"apps/{app['label']}/")
    }
    if owner_app:
        related_apps.add(owner_app)
    if target.startswith("templates/"):
        related_apps.update(app["label"] for app in inventory["apps"])

    report = {
        "target": normalised,
        "owner_app": owner_app,
        "related_apps": sorted(related_apps),
        "routes": routes,
        "templates": templates,
        "frontend_assets": _match_frontend_assets(inventory, normalised),
        "tests": tests,
        "summary": {
            "route_count": len(routes),
            "template_count": len(templates),
            "asset_count": len(_match_frontend_assets(inventory, normalised)),
            "test_count": len(tests),
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    """Print or write the feature impact report for ``--target``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", required=True, help="file, app, or template target to analyze"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args(argv)

    try:
        report = build_feature_impact_report(args.target, args.root.resolve())
        serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"feature-impact: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
