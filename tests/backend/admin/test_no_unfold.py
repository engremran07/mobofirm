"""Guard: the third-party django-unfold theme is gone from code, config and assets."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.backend.admin.theme_guard import REMOVED_THEME

ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = Path(__file__).resolve()
PATTERN = re.compile(REMOVED_THEME, re.IGNORECASE)
SCANNED_DIRS = ("apps", "app", "templates", "tests", "frontend/src")
SCANNED_FILES = ("pyproject.toml", "package.json", "vite.config.js")
TEXT_SUFFIXES = {
    ".py",
    ".html",
    ".htm",
    ".txt",
    ".js",
    ".mjs",
    ".css",
    ".scss",
    ".json",
    ".toml",
    ".md",
    ".yml",
    ".yaml",
    ".svg",
}
IGNORED_PARTS = {"__pycache__", "node_modules"}


def _files() -> list[Path]:
    found = [ROOT / name for name in SCANNED_FILES if (ROOT / name).is_file()]
    for directory in SCANNED_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        found.extend(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix in TEXT_SUFFIXES
            and not IGNORED_PARTS & set(path.relative_to(ROOT).parts)
            and path.resolve() != THIS_FILE
        )
    return found


def test_scan_covers_the_expected_trees():
    scanned = {path.relative_to(ROOT).parts[0] for path in _files()}

    assert {"apps", "app", "templates", "tests", "pyproject.toml"} <= scanned


def test_unfold_does_not_appear_anywhere_in_first_party_sources():
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(
            {
                f"{number}: {line.strip()[:100]}"
                for number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                )
                if PATTERN.search(line)
            }
        )[:3]
        for path in _files()
        if PATTERN.search(path.read_text(encoding="utf-8", errors="ignore"))
    }

    assert offenders == {}, offenders


def test_unfold_files_and_directories_are_gone():
    leftovers = [
        path.relative_to(ROOT).as_posix()
        for base in (ROOT / "apps", ROOT / "app", ROOT / "templates", ROOT / "tests")
        for path in base.rglob("*")
        if REMOVED_THEME in path.name.lower()
        and "__pycache__" not in path.parts
        and path.resolve() != THIS_FILE
    ]

    assert leftovers == [], leftovers


def test_unfold_package_is_not_installed_as_a_django_app():
    from django.conf import settings

    assert not [app for app in settings.INSTALLED_APPS if REMOVED_THEME in app.lower()]
    assert not hasattr(settings, REMOVED_THEME.upper())


@pytest.mark.parametrize("name", ["package-lock.json"])
def test_lockfile_has_no_unfold_entry(name):
    text = (ROOT / name).read_text(encoding="utf-8")

    assert not PATTERN.search(text)
