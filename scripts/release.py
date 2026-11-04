#!/usr/bin/env python3
"""Validate, prepare, and publish versioned Mobofirm releases."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path

VERSION_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
STABLE_VERSION_PATTERN = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
UNRELEASED_PATTERN = re.compile(r"(?m)^## \[Unreleased\]\s*$")
SECTION_PATTERN = re.compile(r"(?m)^## \[")
RELEASE_SECTION_PATTERN = re.compile(
    r"(?m)^## \[(?P<version>[^\]]+)\] - (?P<date>\d{4}-\d{2}-\d{2})\s*$"
)
CATEGORIES = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
ROOT = Path(__file__).resolve().parents[1]


class ReleaseError(Exception):
    """A release input or repository state is invalid."""


def project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get(
        "project"
    )
    if not isinstance(project, dict):
        raise ReleaseError("pyproject.toml must define a [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
        raise ReleaseError("pyproject.toml must contain a valid SemVer project version")
    return version


def changelog_text(root: Path) -> str:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not changelog.startswith("# Changelog\n"):
        raise ReleaseError("CHANGELOG.md must start with '# Changelog'")
    if len(UNRELEASED_PATTERN.findall(changelog)) != 1:
        raise ReleaseError("CHANGELOG.md must contain exactly one '## [Unreleased]'")
    return changelog


def unreleased_notes(changelog: str) -> str:
    match = UNRELEASED_PATTERN.search(changelog)
    if match is None:
        raise ReleaseError("CHANGELOG.md is missing its Unreleased section")
    next_section = SECTION_PATTERN.search(changelog, match.end())
    end = next_section.start() if next_section else len(changelog)
    return changelog[match.end() : end].strip()


def release_notes(changelog: str, version: str) -> str:
    matches = [
        match
        for match in RELEASE_SECTION_PATTERN.finditer(changelog)
        if match.group("version") == version
    ]
    if len(matches) != 1:
        raise ReleaseError(
            f"CHANGELOG.md must contain exactly one section for {version}"
        )
    match = matches[0]
    next_section = SECTION_PATTERN.search(changelog, match.end())
    end = next_section.start() if next_section else len(changelog)
    notes = changelog[match.end() : end].strip()
    if not notes:
        raise ReleaseError(f"CHANGELOG.md release notes for {version} are empty")
    return notes


def validate_changelog(root: Path) -> None:
    changelog = changelog_text(root)
    unreleased = unreleased_notes(changelog)
    if unreleased:
        headings = re.findall(r"(?m)^### (.+)$", unreleased)
        unknown = set(headings) - CATEGORIES
        if unknown:
            raise ReleaseError(
                "Unreleased changelog categories must use Keep a Changelog headings: "
                + ", ".join(sorted(unknown))
            )

    sections = list(RELEASE_SECTION_PATTERN.finditer(changelog))
    versions = [section.group("version") for section in sections]
    for section in sections:
        if not re.fullmatch(STABLE_VERSION_PATTERN, section.group("version")):
            raise ReleaseError(
                "Released changelog headings must use stable SemVer versions"
            )
        try:
            date.fromisoformat(section.group("date"))
        except ValueError as error:
            raise ReleaseError(
                "Released changelog dates must be valid ISO dates"
            ) from error
    if len(versions) != len(set(versions)):
        raise ReleaseError("CHANGELOG.md contains duplicate released-version sections")


def check_repository(root: Path) -> str:
    version = project_version(root)
    validate_changelog(root)
    return version


def _next_version(version: str, bump: str) -> str:
    if not re.fullmatch(STABLE_VERSION_PATTERN, version):
        raise ReleaseError(
            "Release preparation only bumps stable MAJOR.MINOR.PATCH versions"
        )
    major, minor, patch = (int(part) for part in version.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _require_clean_worktree(root: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        raise ReleaseError("Release preparation requires a clean Git worktree")


def _replace_project_version(pyproject: str, version: str) -> str:
    project_headers = list(re.finditer(r"(?m)^\[project\]\s*$", pyproject))
    if len(project_headers) != 1:
        raise ReleaseError("pyproject.toml must contain exactly one [project] table")
    start = project_headers[0].end()
    next_table = re.search(r"(?m)^\[", pyproject[start:])
    end = start + next_table.start() if next_table else len(pyproject)
    matches = list(
        re.finditer(r'(?m)^version\s*=\s*"([^"]+)"\s*$', pyproject[start:end])
    )
    if len(matches) != 1:
        raise ReleaseError("[project] must define exactly one quoted version")
    version_start = start + matches[0].start(1)
    version_end = start + matches[0].end(1)
    return pyproject[:version_start] + version + pyproject[version_end:]


def prepare_release(root: Path, bump: str) -> str:
    current_version = check_repository(root)
    _require_clean_worktree(root)

    changelog = changelog_text(root)
    notes = unreleased_notes(changelog)
    if not notes or not re.search(
        r"(?m)^### (?:Added|Changed|Deprecated|Removed|Fixed|Security)$", notes
    ):
        raise ReleaseError(
            "Add categorized release notes under [Unreleased] before bumping"
        )
    if not re.search(r"(?m)^[-*] .+", notes):
        raise ReleaseError("Add at least one changelog bullet under [Unreleased]")

    new_version = _next_version(current_version, bump)
    tag = f"v{new_version}"
    existing_tag = subprocess.run(
        ["git", "tag", "--list", tag],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if existing_tag:
        raise ReleaseError(f"Git tag {tag} already exists")

    pyproject_path = root / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")
    updated_pyproject = _replace_project_version(pyproject, new_version)

    changelog_path = root / "CHANGELOG.md"
    unreleased_header = UNRELEASED_PATTERN.search(changelog)
    if unreleased_header is None:
        raise ReleaseError("CHANGELOG.md is missing its Unreleased section")
    section_start = unreleased_header.start()
    next_section = SECTION_PATTERN.search(changelog, unreleased_header.end())
    section_end = next_section.start() if next_section else len(changelog)
    released_notes = changelog[unreleased_header.end() : section_end].strip()
    remaining_sections = changelog[section_end:].lstrip("\r\n")
    release_date = datetime.now(UTC).date().isoformat()
    updated_changelog = (
        changelog[:section_start]
        + "## [Unreleased]\n\n"
        + f"## [{new_version}] - {release_date}\n\n"
        + released_notes
        + ("\n\n" + remaining_sections if remaining_sections else "")
        + "\n"
    )

    pyproject_path.write_text(updated_pyproject, encoding="utf-8")
    changelog_path.write_text(updated_changelog, encoding="utf-8")
    return new_version


def validate_tag(root: Path, tag: str) -> str:
    version = check_repository(root)
    match = re.fullmatch(r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))", tag)
    if match is None or match.group(1) != version:
        raise ReleaseError(
            f"Tag must be v{version} to match the canonical pyproject.toml version"
        )
    release_notes(changelog_text(root), version)
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate release metadata and changelog")
    bump_parser = subparsers.add_parser(
        "bump", help="prepare a stable semantic release"
    )
    bump_parser.add_argument("level", choices=("major", "minor", "patch"))
    tag_parser = subparsers.add_parser(
        "validate-tag", help="validate a release tag against project metadata"
    )
    tag_parser.add_argument("tag")
    notes_parser = subparsers.add_parser(
        "notes", help="print the changelog section for a release tag"
    )
    notes_parser.add_argument("tag")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        if args.command == "check":
            print(f"Release metadata valid: {check_repository(root)}")
        elif args.command == "bump":
            version = prepare_release(root, args.level)
            print(
                f"Prepared {version}; review and commit pyproject.toml and CHANGELOG.md"
            )
        elif args.command == "validate-tag":
            print(f"Release tag valid: v{validate_tag(root, args.tag)}")
        else:
            version = validate_tag(root, args.tag)
            print(release_notes(changelog_text(root), version))
    except ReleaseError as error:
        print(f"release: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
