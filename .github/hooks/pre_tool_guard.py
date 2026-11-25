"""Copilot PreToolUse guardrails for destructive commands and sensitive paths."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

BLOCKED_COMMAND_PATTERNS = (
    re.compile(
        r"\b(?:rm\b[^\r\n]*(?:-rf\b|-fr\b|(?<!\S)-r(?:\s+-f)?\b|--recursive\b)|"
        r"remove-item\b[^\r\n]*-recurse\b|"
        r"(?:rmdir|rd|del)\b[^\r\n]*/[sq]\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:drop\s+(?:database|table)|truncate(?:\s+table)?|"
        r"manage\.py\s+(?:flush|reset_db)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bgit\s+push\b[^\r\n]*(?:--force(?:-with-lease)?\b|(?:^|\s)-f(?:\s|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-[^\r\n]*[fd]", re.IGNORECASE
    ),
    re.compile(
        r"\bgit\s+checkout\s+--(?:\s|$)|\bgit\s+restore\s+--worktree\b",
        re.IGNORECASE,
    ),
)
EDIT_TOOL_PATTERN = re.compile(r"(?:edit|write|create|replace|patch)", re.IGNORECASE)
PATCH_PATH_PATTERN = re.compile(
    r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", re.MULTILINE
)
SENSITIVE_PATH_PARTS = {".env", "secrets"}
PATH_KEYS = {"file", "filepath", "file_path", "path", "target", "target_file"}


def _strings(value: Any) -> Sequence[str]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(text for item in value.values() for text in _strings(item))
    if isinstance(value, list):
        return tuple(text for item in value for text in _strings(item))
    return ()


def _paths(value: Any) -> Sequence[str]:
    if isinstance(value, Mapping):
        found = [
            child
            for key, child in value.items()
            if key.lower() in PATH_KEYS and isinstance(child, str)
        ]
        return tuple(found) + tuple(
            path
            for key, child in value.items()
            if key.lower() not in PATH_KEYS
            for path in _paths(child)
        )
    if isinstance(value, list):
        return tuple(path for item in value for path in _paths(item))
    return ()


def _patch_paths(value: Any) -> Sequence[str]:
    return tuple(
        match.group(1).strip()
        for text in _strings(value)
        for match in PATCH_PATH_PATTERN.finditer(text)
    )


def _is_sensitive_path(raw_path: str) -> bool:
    parts = tuple(part.lower() for part in raw_path.replace("\\", "/").split("/"))
    if any(part in SENSITIVE_PATH_PARTS or part.startswith(".env.") for part in parts):
        return True
    if any(
        "credential" in part
        or "private_key" in part
        or part.endswith((".pem", ".key", ".p12", ".pfx"))
        for part in parts
    ):
        return True
    return any(
        parts[index : index + 2] == (".github", "hooks")
        for index in range(len(parts) - 1)
    )


def evaluate(payload: Mapping[str, Any]) -> dict[str, str]:
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or "")
    tool_args = payload.get("toolArgs", payload.get("tool_input", {}))

    if EDIT_TOOL_PATTERN.search(tool_name):
        for path in (*_paths(tool_args), *_patch_paths(tool_args)):
            if _is_sensitive_path(path):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "This workspace guard blocks agent edits to secrets and hook "
                        "scripts. Review and make those changes manually."
                    ),
                }

    if re.search(
        r"(?:shell|terminal|bash|powershell|command)", tool_name, re.IGNORECASE
    ):
        command = "\n".join(_strings(tool_args))
        for pattern in BLOCKED_COMMAND_PATTERNS:
            if pattern.search(command):
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "This workspace guard blocks a destructive command pattern. "
                        "Verify the target and use an explicitly authorized workflow."
                    ),
                }

    return {}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "The workspace guard could not parse the tool event."
                    ),
                }
            )
        )
        return

    if not isinstance(payload, Mapping):
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "The workspace guard received an invalid tool event."
                    ),
                }
            )
        )
        return

    print(json.dumps(evaluate(payload)))


if __name__ == "__main__":
    main()
