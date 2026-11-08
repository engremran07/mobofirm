"""Apps are self-contained: cross-app imports follow documented, ratcheted rules.

The rules, in plain terms (see ``docs/04-backend/app-dependencies.md``):

1. An app never imports another app's ``admin``, ``views``, ``urls`` or ``forms``.
2. ``dialogs`` may only be imported from ``core`` (the shared dialog shell).
3. ``core`` is shared infrastructure and never imports the newer domain apps.
4. Every other edge must already be in the measured baseline. New dependencies need a
   deliberate update of ``tests/backend/app_boundaries_baseline.py``.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from tests.backend.app_boundaries_baseline import ALLOWED_EDGES

ROOT = Path(__file__).resolve().parents[2]
APPS_DIR = ROOT / "apps"
SKIPPED_PARTS = {"migrations", "__pycache__"}
FORBIDDEN_LAYERS = frozenset({"admin", "views", "urls", "forms"})
CORE_MAY_NOT_IMPORT = frozenset(
    {
        "bounty",
        "consent",
        "distribution",
        "gamification",
        "marketplace",
        "pages",
        "promotions",
        "referral",
        "seo",
        "shop",
        "storage",
        "wallet",
    }
)


def _app_names() -> list[str]:
    return sorted(
        path.name
        for path in APPS_DIR.iterdir()
        if path.is_dir() and (path / "apps.py").exists()
    )


def _cross_app_imports() -> dict[tuple[str, str], set[str]]:
    names = set(_app_names())
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for owner in names:
        for source in (APPS_DIR / owner).rglob("*.py"):
            if SKIPPED_PARTS & set(source.parts):
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    parts = module.split(".")
                    if len(parts) >= 2 and parts[0] == "apps" and parts[1] in names:
                        target = parts[1]
                        if target != owner:
                            layer = parts[2] if len(parts) > 2 else "(package)"
                            found[(owner, target)].add(layer)
    return found


def test_apps_do_not_import_other_apps_admin_views_urls_or_forms():
    offenders = [
        f"{owner}->{target}.{layer}"
        for (owner, target), layers in _cross_app_imports().items()
        for layer in layers
        if layer in FORBIDDEN_LAYERS
    ]

    assert offenders == [], (
        "Expose a service or contract instead of importing another app's UI layer: "
        f"{sorted(offenders)}"
    )


def test_only_core_provides_the_dialog_shell():
    offenders = [
        f"{owner}->{target}.dialogs"
        for (owner, target), layers in _cross_app_imports().items()
        if "dialogs" in layers and target != "core"
    ]

    assert offenders == [], (
        f"Dialogs belong to the app that owns the behaviour: {sorted(offenders)}"
    )


def test_core_never_imports_the_newer_domain_apps():
    offenders = sorted(
        f"core->{target}"
        for (owner, target) in _cross_app_imports()
        if owner == "core" and target in CORE_MAY_NOT_IMPORT
    )

    assert offenders == [], (
        f"core is shared infrastructure; use events or contracts: {offenders}"
    )


def test_no_new_cross_app_dependency_without_updating_the_baseline():
    new_edges: list[str] = []
    new_layers: list[str] = []
    for (owner, target), layers in sorted(_cross_app_imports().items()):
        edge = f"{owner}->{target}"
        allowed = ALLOWED_EDGES.get(edge)
        if allowed is None:
            new_edges.append(f"{edge} ({', '.join(sorted(layers))})")
        else:
            extra = layers - allowed
            if extra:
                new_layers.append(f"{edge}: {', '.join(sorted(extra))}")

    assert new_edges == [], (
        "New cross-app dependency. Prefer an event or a documented contract; if it is "
        "justified, add it to tests/backend/app_boundaries_baseline.py and "
        f"docs/04-backend/app-dependencies.md: {new_edges}"
    )
    assert new_layers == [], f"New modules used across apps: {new_layers}"


def test_every_installed_domain_app_has_a_standalone_app_config():
    missing = [
        name for name in _app_names() if not (APPS_DIR / name / "__init__.py").exists()
    ]

    assert missing == []
