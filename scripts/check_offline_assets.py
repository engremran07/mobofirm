#!/usr/bin/env python3
"""Fail when the built public site or staff console has remote local assets."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

PUBLIC_BUILD_DIR = Path(__file__).resolve().parent.parent / "static" / "build"
ADMIN_BUILD_DIR = PUBLIC_BUILD_DIR / "admin"
BUILD_DIR = ADMIN_BUILD_DIR
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


def check_manifest(build_dir: Path = BUILD_DIR) -> list[str]:
    """Validate the staff-console Vite manifest and every referenced output."""
    manifest_path = build_dir / ".vite" / "manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path}: missing; run `npm run build:admin` first"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{manifest_path}: invalid JSON ({error})"]
    problems: list[str] = []
    entries = [entry for entry in manifest.values() if entry.get("isEntry")]
    if not entries:
        problems.append(f"{manifest_path}: no entry chunks")
    for key, entry in manifest.items():
        for name in [entry.get("file"), *entry.get("css", [])]:
            if name and not (build_dir / name).is_file():
                problems.append(
                    f"{manifest_path}: {key} references missing file {name}"
                )
    return problems


def check_build(
    build_dir: Path,
    *,
    manifest_required: bool,
    build_command: str,
    excluded_dir: Path | None = None,
) -> list[str]:
    """Check built assets for one shell, optionally excluding a nested bundle."""
    if not build_dir.is_dir():
        return [f"{build_dir}: missing; run `{build_command}` first"]
    problems = check_manifest(build_dir) if manifest_required else []
    for path in sorted(build_dir.rglob("*")):
        if excluded_dir is not None and path.is_relative_to(excluded_dir):
            continue
        if path.is_file() and path.suffix.lower() in SCANNED_SUFFIXES:
            problems.extend(scan_file(path))
    return problems


def main() -> int:
    problems = check_build(
        PUBLIC_BUILD_DIR,
        manifest_required=False,
        build_command="npm run build:public",
        excluded_dir=ADMIN_BUILD_DIR,
    )
    problems.extend(
        check_build(
            ADMIN_BUILD_DIR,
            manifest_required=True,
            build_command="npm run build:admin",
        )
    )
    if problems:
        print("Offline asset check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"Offline asset check passed for {PUBLIC_BUILD_DIR} and {ADMIN_BUILD_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
