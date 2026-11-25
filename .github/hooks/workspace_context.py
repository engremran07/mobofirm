"""Report repository facts at session start without modifying the workspace."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def workspace_summary(root: Path) -> str:
    apps_dir = root / "apps"
    present_apps = (
        sorted(
            app_dir.name
            for app_dir in apps_dir.iterdir()
            if app_dir.is_dir() and (app_dir / "apps.py").is_file()
        )
        if apps_dir.is_dir()
        else []
    )
    project_files = {
        "Django entry point": (root / "manage.py").is_file(),
        "Python project manifest": any(
            (root / name).is_file()
            for name in ("pyproject.toml", "requirements.txt", "Pipfile")
        ),
        "Frontend manifest": (root / "package.json").is_file(),
        "Flutter manifest": (root / "pubspec.yaml").is_file(),
        "Android project": (root / "gradlew").is_file()
        or (root / "gradlew.bat").is_file(),
    }
    found = ", ".join(present_apps) if present_apps else "none"
    status = "; ".join(
        f"{label}: {'present' if exists else 'absent'}"
        for label, exists in project_files.items()
    )
    return (
        f"Workspace inventory: local app packages present under apps/: {found}. "
        f"{status}. "
        "Base implementation and validation claims on files and tools actually present."
    )


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    sys.stdin.read()
    print(json.dumps({"additionalContext": workspace_summary(root)}))


if __name__ == "__main__":
    main()
