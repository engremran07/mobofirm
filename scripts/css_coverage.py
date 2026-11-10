"""List CSS classes used by templates but defined nowhere in static/build CSS.

Usage: python scripts/css_coverage.py [template-subdir ...]
Exit status 1 when any class is missing, so it can gate a migration.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "static" / "build"
TEMPLATES = ROOT / "templates"
CLASS_ATTR = re.compile(r'\bclass\s*=\s*"([^"]*)"')
TAG_BLOCK = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.DOTALL)
SELECTOR_CLASS = re.compile(r"\.(-?[_a-zA-Z][\w-]*|\\[^\s{,.:>+~)\[]+)")
# Hooks used only by JavaScript, tests or Alpine, never styled.
IGNORED_PREFIXES = ("js-", "lucide", "x-", "hx-")
IGNORED = {"icon", "visually-hidden"}


def defined_classes() -> set[str]:
    """Every class that appears in a selector of any built stylesheet."""
    found: set[str] = set()
    for sheet in BUILD.glob("*.css"):
        text = sheet.read_text(encoding="utf-8", errors="ignore")
        for match in SELECTOR_CLASS.finditer(text):
            found.add(match.group(1).replace("\\", ""))
    return found


VARIABLE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
BLOCK_MARK = "\x00"
VAR_MARK = "\x01"


def used_classes(path: Path) -> tuple[set[str], set[str]]:
    """Return (static tokens, dynamic tokens) of a template.

    A dynamic token glues a ``{{ variable }}`` (marked ``\\x01``) or a ``{% tag %}``
    (marked ``\\x00``) to literal text, e.g. ``badge-ui--{{ tone }}``.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    static: set[str] = set()
    dynamic: set[str] = set()
    for attr in CLASS_ATTR.findall(text):
        cleaned = re.sub(r"\{#.*?#\}", " ", attr, flags=re.DOTALL)
        cleaned = VARIABLE.sub(VAR_MARK, cleaned)
        cleaned = re.sub(r"\{%.*?%\}", BLOCK_MARK, cleaned, flags=re.DOTALL)
        for chunk in cleaned.split():
            if BLOCK_MARK in chunk or VAR_MARK in chunk:
                dynamic.add(chunk)
                continue
            if re.fullmatch(r"-?[_a-zA-Z][\w-]*", chunk):
                static.add(chunk)
    return static, dynamic


def missing_dynamic(token: str, defined: set[str]) -> list[str]:
    """Describe the parts of a dynamic token that no stylesheet can match."""
    parts = re.split(f"[{BLOCK_MARK}{VAR_MARK}]", token)
    base = parts[0]
    problems: list[str] = []
    if VAR_MARK in token:
        suffix = parts[-1]
        if not any(
            name.startswith(base) and name.endswith(suffix) for name in defined
        ):
            problems.append(f"{base}*{suffix}")
        return problems
    if base and not base.endswith(("-", "_")) and base not in defined:
        problems.append(base)
    problems.extend(
        base + alternative
        for alternative in parts[1:]
        if alternative and base + alternative not in defined
    )
    return problems


def main() -> int:
    """List template classes that no built stylesheet defines."""
    wanted = sys.argv[1:] or ["."]
    defined = defined_classes()
    total_missing = 0
    for sub in wanted:
        for path in sorted((TEMPLATES / sub).rglob("*.html")):
            static, dynamic = used_classes(path)
            missing = sorted(
                token
                for token in static
                if token not in defined
                and token not in IGNORED
                and not token.startswith(IGNORED_PREFIXES)
            )
            for token in sorted(dynamic):
                if not token.startswith(IGNORED_PREFIXES):
                    missing.extend(missing_dynamic(token, defined))
            if missing:
                total_missing += len(missing)
                relative = path.relative_to(TEMPLATES).as_posix()
                print(f"{relative}: {' '.join(missing)}")
    print(f"missing class references: {total_missing}")
    return 1 if total_missing else 0


if __name__ == "__main__":
    sys.exit(main())
