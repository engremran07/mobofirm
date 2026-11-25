"""Report Ruff findings for Python files changed by Copilot file-edit tools."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

EDIT_TOOLS = ("edit", "write", "create", "replace", "patch")
PATH_KEYS = {"file", "filepath", "file_path", "path", "target", "target_file"}
PATCH_PATH_PATTERN = re.compile(
    r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", re.MULTILINE
)


def extract_paths(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        paths = [
            child
            for key, child in value.items()
            if key.lower() in PATH_KEYS and isinstance(child, str)
        ]
        paths.extend(
            match.group(1).strip()
            for key, child in value.items()
            if isinstance(child, str) and key.lower() in {"patch", "diff"}
            for match in PATCH_PATH_PATTERN.finditer(child)
        )
        for key, child in value.items():
            if key.lower() not in PATH_KEYS:
                paths.extend(extract_paths(child))
        return paths
    if isinstance(value, list):
        return [path for item in value for path in extract_paths(item)]
    return []


def ruff_path(root: Path) -> Path | None:
    candidates = (
        root / ".venv" / "Scripts" / "ruff.exe",
        root / ".venv" / "bin" / "ruff",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def lint_paths(root: Path, paths: list[str]) -> list[str]:
    executable = ruff_path(root)
    if executable is None:
        return ["Ruff was not found in the repository virtual environment."]

    messages: list[str] = []
    checked: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_relative_to(root):
            continue
        if path in checked or path.suffix != ".py" or not path.is_file():
            continue
        checked.add(path)
        try:
            result = subprocess.run(
                [str(executable), "check", "--output-format=concise", str(path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=12,
                cwd=root,
            )
        except FileNotFoundError:
            messages.append(f"Ruff could not be started for {path}.")
            continue
        except subprocess.TimeoutExpired:
            messages.append(f"Ruff timed out while checking {path}.")
            continue
        if result.returncode:
            output = (result.stdout + result.stderr).strip()
            messages.append(f"Ruff findings in {path}:\n{output[-1200:]}")
    return messages


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(
            json.dumps(
                {
                    "additionalContext": (
                        "Post-edit Ruff check skipped because the hook event "
                        "could not be parsed."
                    )
                }
            )
        )
        return
    if not isinstance(payload, Mapping):
        print(
            json.dumps(
                {
                    "additionalContext": (
                        "Post-edit Ruff check skipped because the hook event "
                        "was not an object."
                    )
                }
            )
        )
        return

    tool_name = str(payload.get("toolName") or payload.get("tool_name") or "")
    if not any(name in tool_name.lower() for name in EDIT_TOOLS):
        return

    tool_args = payload.get("toolArgs", payload.get("tool_input", {}))
    root = Path(__file__).resolve().parents[2]
    messages = lint_paths(root, extract_paths(tool_args))
    if messages:
        print(json.dumps({"additionalContext": "\n\n".join(messages)}))


if __name__ == "__main__":
    main()
