"""Every literal app label used with the registry has a flag (F-013 contract)."""

from __future__ import annotations

import ast
from pathlib import Path

from apps.core.app_service import registry_flag_names

APPS_DIR = Path(__file__).resolve().parents[3] / "apps"
LABEL_CALLS = {"app_enabled", "require_app_enabled"}
SERVICE_METHODS = {"get", "export"}


def literal_labels() -> dict[str, set[str]]:
    """Map each literal label to the files that pass it to the registry helpers."""
    found: dict[str, set[str]] = {}
    for path in APPS_DIR.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            func = node.func
            is_helper = isinstance(func, ast.Name) and func.id in LABEL_CALLS
            is_service = (
                isinstance(func, ast.Attribute)
                and func.attr in SERVICE_METHODS
                and isinstance(func.value, ast.Name)
                and func.value.id == "AppService"
            )
            if is_helper or is_service:
                found.setdefault(first.value, set()).add(
                    str(path.relative_to(APPS_DIR))
                )
    return found


def test_the_scan_finds_the_known_call_sites():
    labels = literal_labels()

    assert {"wallet", "shop", "blog", "promotions"} <= set(labels)


def test_every_label_used_in_the_apps_has_an_app_registry_flag():
    unknown = {
        label: sorted(files)
        for label, files in literal_labels().items()
        if label not in registry_flag_names()
    }

    assert unknown == {}, (
        "These labels have no AppRegistry '<label>_enabled' flag, so the switch "
        f"cannot work: {unknown}"
    )
