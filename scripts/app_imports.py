"""Map cross-app imports between the first-party Django apps (read-only helper)."""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "apps"
SKIP_PARTS = {"migrations", "__pycache__"}


def app_names() -> list[str]:
    return sorted(
        path.name
        for path in APPS.iterdir()
        if path.is_dir() and (path / "apps.py").exists()
    )


def iter_imports(source: Path):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name


def main() -> None:
    names = app_names()
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for owner in names:
        for source in (APPS / owner).rglob("*.py"):
            if SKIP_PARTS & set(source.parts):
                continue
            for _line, module in iter_imports(source):
                parts = module.split(".")
                if len(parts) >= 2 and parts[0] == "apps" and parts[1] in names:
                    target = parts[1]
                    if target != owner:
                        layer = parts[2] if len(parts) > 2 else "(package)"
                        found[(owner, target)].add(layer)
    summary = {
        f"{owner}->{target}": sorted(layers)
        for (owner, target), layers in sorted(found.items())
    }
    json.dump(summary, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
