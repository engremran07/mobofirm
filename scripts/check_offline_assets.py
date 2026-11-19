#!/usr/bin/env python3
"""Fail when the built staff console is not fully offline and manifest-consistent."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

BUILD_DIR = Path(__file__).resolve().parent.parent / "static" / "build" / "admin"
MANIFEST = BUILD_DIR / ".vite" / "manifest.json"
SCANNED_SUFFIXES = {".css", ".js", ".mjs", ".html", ".svg"}

URL_PATTERN = re.compile(
    r"""(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s"'`)<>\\]*"""
)
CSS_REMOTE_PATTERN = re.compile(
    r"""(?:url\(\s*|@import\s+(?:url\(\s*)?)["']?(?:https?:)?//""", re.IGNORECASE
)
# XML namespace identifiers inside inline SVG/data URIs are never fetched.
NAMESPACE_URLS = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xhtml",
}
CDN_HOST_MARKERS = (
    "cdn.",
    "cdnjs.",
    "jsdelivr.",
    "unpkg.",
    "googleapis.",
    "gstatic.",
    "bootstrapcdn.",
    "cloudflare.",
    "fontawesome.",
    "typekit.",
    "fonts.",
    "code.jquery.",
    "ajax.aspnetcdn.",
)
FETCHABLE_SUFFIXES = (".js", ".mjs", ".css", ".woff", ".woff2", ".ttf", ".otf", ".eot")


def is_remote_asset(url: str) -> bool:
    """Return True for URLs that point at a CDN host or a fetchable script/style/font."""
    if url.rstrip("/") in NAMESPACE_URLS or url.startswith("//www.w3.org/"):
        return False
    parts = urlsplit(url if not url.startswith("//") else f"https:{url}")
    host = (parts.hostname or "").lower()
    if any(
        marker in f"{host}." or host.startswith(marker) for marker in CDN_HOST_MARKERS
    ):
        return True
    return parts.path.lower().endswith(FETCHABLE_SUFFIXES)


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    findings = [
        f"{path}: remote reference in CSS: {match.group(0)}"
        for match in CSS_REMOTE_PATTERN.finditer(text)
    ]
    for match in URL_PATTERN.finditer(text):
        url = match.group(0)
        if is_remote_asset(url):
            findings.append(f"{path}: external asset URL: {url}")
    return findings


def check_manifest() -> list[str]:
    if not MANIFEST.is_file():
        return [f"{MANIFEST}: missing; run `npm run build:admin` first"]
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{MANIFEST}: invalid JSON ({error})"]
    problems: list[str] = []
    entries = [entry for entry in manifest.values() if entry.get("isEntry")]
    if not entries:
        problems.append(f"{MANIFEST}: no entry chunks")
    for key, entry in manifest.items():
        for name in [entry.get("file"), *entry.get("css", [])]:
            if name and not (BUILD_DIR / name).is_file():
                problems.append(f"{MANIFEST}: {key} references missing file {name}")
    return problems


def main() -> int:
    problems = check_manifest()
    for path in sorted(BUILD_DIR.rglob("*")):
        if path.is_file() and path.suffix.lower() in SCANNED_SUFFIXES:
            problems.extend(scan_file(path))
    if problems:
        print("Offline asset check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"Offline asset check passed for {BUILD_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
