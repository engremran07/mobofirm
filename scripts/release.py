#!/usr/bin/env python3
"""Validate, prepare, and publish versioned Mobofirm releases."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
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
LINK_PATTERN = re.compile(r"(?m)^\[(?P<label>Unreleased|[^\]\s]+)\]: (?P<url>\S+)\s*$")
CATEGORIES = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = ("pyproject.toml", "CHANGELOG.md")


class ReleaseError(Exception):
    """A release input or repository state is invalid."""


@dataclass(frozen=True)
class ReleasePlan:
    """What a ``bump`` does (or, with ``dry_run``, would do)."""

    previous: str
    version: str
    release_date: str
    repo_url: str | None

    @property
    def tag(self) -> str:
        """The release tag for this version."""
        return f"v{self.version}"


def project_version(root: Path) -> str:
    """Return the validated SemVer ``[project] version`` from pyproject.toml."""
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
    """Read CHANGELOG.md and validate its heading and Unreleased section."""
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not changelog.startswith("# Changelog\n"):
        raise ReleaseError("CHANGELOG.md must start with '# Changelog'")
    if len(UNRELEASED_PATTERN.findall(changelog)) != 1:
        raise ReleaseError("CHANGELOG.md must contain exactly one '## [Unreleased]'")
    return changelog


def _links_start(changelog: str) -> int:
    """Offset where the trailing link-reference block starts (end of text if none)."""
    start = len(changelog)
    position = len(changelog)
    for line in reversed(changelog.splitlines(keepends=True)):
        position -= len(line)
        if LINK_PATTERN.fullmatch(line.strip()):
            start = position
        elif line.strip():
            break
    return start


def _section_end(changelog: str, start: int) -> int:
    next_section = SECTION_PATTERN.search(changelog, start)
    section_end = next_section.start() if next_section else len(changelog)
    return min(section_end, _links_start(changelog))


def unreleased_notes(changelog: str) -> str:
    """Return the body of the Unreleased section."""
    match = UNRELEASED_PATTERN.search(changelog)
    if match is None:
        raise ReleaseError("CHANGELOG.md is missing its Unreleased section")
    return changelog[match.end() : _section_end(changelog, match.end())].strip()


def release_notes(changelog: str, version: str) -> str:
    """Return the non-empty notes of the single released section for ``version``."""
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
    notes = changelog[match.end() : _section_end(changelog, match.end())].strip()
    if not notes:
        raise ReleaseError(f"CHANGELOG.md release notes for {version} are empty")
    return notes


def validate_changelog(root: Path) -> None:
    """Raise ``ReleaseError`` unless the changelog follows Keep a Changelog."""
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
    _validate_links(changelog, versions)


def _validate_links(changelog: str, versions: list[str]) -> None:
    """When link references exist they must cover every release consistently."""
    links = {
        match.group("label"): match.group("url")
        for match in LINK_PATTERN.finditer(changelog[_links_start(changelog) :])
    }
    if not links:
        return
    missing = [label for label in ["Unreleased", *versions] if label not in links]
    if missing:
        raise ReleaseError(
            "CHANGELOG.md link references are missing for: " + ", ".join(missing)
        )
    if versions and not links["Unreleased"].endswith(f"/compare/v{versions[0]}...HEAD"):
        raise ReleaseError(
            f"[Unreleased] link must compare v{versions[0]}...HEAD (latest release)"
        )


def check_repository(root: Path) -> str:
    """Validate release metadata and return the project version."""
    version = project_version(root)
    validate_changelog(root)
    return version


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ReleaseError(f"git {args[0]} failed: {detail.strip()}") from error
    return result.stdout.strip()


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
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ReleaseError(
            "Release preparation requires a clean Git worktree "
            "(use --allow-dirty only to prepare files that someone else commits)"
        )


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


def repository_url(root: Path, override: str | None = None) -> str | None:
    """The public ``https://host/owner/repo`` URL from ``--repo-url`` or ``origin``."""
    raw = override
    if raw is None:
        try:
            raw = _git(root, "remote", "get-url", "origin")
        except ReleaseError:
            return None
    match = re.fullmatch(
        r"(?:https?://|git@|ssh://git@)(?P<host>[^/:]+)[/:](?P<path>[^\s]+?)(?:\.git)?/?",
        raw.strip(),
    )
    if match is None:
        return None
    return f"https://{match.group('host')}/{match.group('path')}"


def _link_references(changelog: str, repo_url: str) -> str:
    """Keep a Changelog link block for ``[Unreleased]`` and every release section."""
    versions = [
        match.group("version") for match in RELEASE_SECTION_PATTERN.finditer(changelog)
    ]
    lines = []
    if versions:
        lines.append(f"[Unreleased]: {repo_url}/compare/v{versions[0]}...HEAD")
    else:
        lines.append(f"[Unreleased]: {repo_url}/commits/HEAD")
    for index, version in enumerate(versions):
        if index + 1 < len(versions):
            lines.append(
                f"[{version}]: {repo_url}/compare/v{versions[index + 1]}...v{version}"
            )
        else:
            lines.append(f"[{version}]: {repo_url}/releases/tag/v{version}")
    return "\n".join(lines) + "\n"


def with_link_references(changelog: str, repo_url: str | None) -> str:
    """Return ``changelog`` with its trailing link block regenerated (idempotent)."""
    if repo_url is None:
        return changelog
    body = changelog[: _links_start(changelog)].rstrip() + "\n"
    return body + "\n" + _link_references(body, repo_url)


def plan_release(
    root: Path,
    bump: str,
    *,
    today: date | None = None,
    repo_url: str | None = None,
    allow_dirty: bool = False,
) -> ReleasePlan:
    """Validate that ``bump`` can run and describe it, without changing anything."""
    current_version = check_repository(root)
    if not allow_dirty:
        _require_clean_worktree(root)

    notes = unreleased_notes(changelog_text(root))
    if not notes or not re.search(
        r"(?m)^### (?:Added|Changed|Deprecated|Removed|Fixed|Security)$", notes
    ):
        raise ReleaseError(
            "Add categorized release notes under [Unreleased] before bumping"
        )
    if not re.search(r"(?m)^[-*] .+", notes):
        raise ReleaseError("Add at least one changelog bullet under [Unreleased]")

    new_version = _next_version(current_version, bump)
    _require_unused_tag(root, f"v{new_version}")
    resolved_url = repository_url(root, repo_url)
    changelog = changelog_text(root)
    if resolved_url is None and _links_start(changelog) < len(changelog):
        raise ReleaseError(
            "CHANGELOG.md has link references but the repository URL is unknown; "
            "pass --repo-url https://host/owner/repo"
        )
    return ReleasePlan(
        previous=current_version,
        version=new_version,
        release_date=(today or datetime.now(UTC).date()).isoformat(),
        repo_url=resolved_url,
    )


def prepare_release(root: Path, plan: ReleasePlan) -> None:
    """Write the bumped version and the promoted changelog for ``plan``."""
    pyproject_path = root / "pyproject.toml"
    changelog_path = root / "CHANGELOG.md"
    original_pyproject = pyproject_path.read_text(encoding="utf-8")
    original_changelog = changelog_path.read_text(encoding="utf-8")
    updated_pyproject = _replace_project_version(original_pyproject, plan.version)
    updated_changelog = with_link_references(
        _promote_unreleased(original_changelog, plan.version, plan.release_date),
        plan.repo_url,
    )
    try:
        pyproject_path.write_text(updated_pyproject, encoding="utf-8")
        changelog_path.write_text(updated_changelog, encoding="utf-8")
    except OSError as error:
        pyproject_path.write_text(original_pyproject, encoding="utf-8")
        changelog_path.write_text(original_changelog, encoding="utf-8")
        raise ReleaseError(f"could not write release files: {error}") from error


def _require_unused_tag(root: Path, tag: str) -> None:
    if _git(root, "tag", "--list", tag):
        raise ReleaseError(f"Git tag {tag} already exists")


def _promote_unreleased(changelog: str, new_version: str, release_date: str) -> str:
    unreleased_header = UNRELEASED_PATTERN.search(changelog)
    if unreleased_header is None:
        raise ReleaseError("CHANGELOG.md is missing its Unreleased section")
    section_end = _section_end(changelog, unreleased_header.end())
    released_notes = changelog[unreleased_header.end() : section_end].strip()
    remaining = changelog[section_end:]
    return (
        changelog[: unreleased_header.start()]
        + "## [Unreleased]\n\n"
        + f"## [{new_version}] - {release_date}\n\n"
        + released_notes
        + "\n\n"
        + remaining.lstrip("\r\n")
    ).rstrip() + "\n"


def commit_release(root: Path, plan: ReleasePlan) -> None:
    """Commit exactly the two release files (never pushes)."""
    _git(root, "add", *RELEASE_FILES)
    _git(
        root,
        "commit",
        "--quiet",
        "-m",
        f"Release {plan.tag}",
        "--only",
        *RELEASE_FILES,
    )


def tag_release(root: Path, version: str, *, dry_run: bool = False) -> str:
    """Create the annotated ``v<version>`` tag on HEAD; idempotent when already there.

    Returns a human-readable outcome. Refuses a dirty worktree, a version without a
    changelog section, and a tag that already points at another commit.
    """
    tag = f"v{version}"
    release_notes(changelog_text(root), version)
    _require_clean_worktree(root)
    if _git(root, "tag", "--list", tag):
        if _git(root, "rev-list", "-n", "1", tag) == _git(root, "rev-parse", "HEAD"):
            return f"Tag {tag} already exists on HEAD; nothing to do"
        raise ReleaseError(f"Git tag {tag} already exists on a different commit")
    if dry_run:
        return f"Dry run: would create annotated tag {tag} on HEAD"
    _git(root, "tag", "-a", tag, "-m", f"Mobofirm {version}")
    return f"Created annotated tag {tag} on HEAD (push it yourself after review)"


def validate_tag(root: Path, tag: str) -> str:
    """Return the project version after checking ``tag`` matches it."""
    version = check_repository(root)
    match = re.fullmatch(r"v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))", tag)
    if match is None or match.group(1) != version:
        raise ReleaseError(
            f"Tag must be v{version} to match the canonical pyproject.toml version"
        )
    release_notes(changelog_text(root), version)
    return version


def _run_bump(root: Path, args: argparse.Namespace) -> None:
    if (args.commit or args.tag) and args.allow_dirty:
        raise ReleaseError("--commit/--tag cannot be combined with --allow-dirty")
    if args.tag and not args.commit:
        raise ReleaseError("--tag requires --commit (the tag marks the release commit)")
    today = date.fromisoformat(args.date) if args.date else None
    plan = plan_release(
        root,
        args.level,
        today=today,
        repo_url=args.repo_url,
        allow_dirty=args.allow_dirty,
    )
    summary = f"{plan.previous} -> {plan.version} ({plan.release_date})"
    if args.dry_run:
        print(f"Dry run: would release {summary}; no files changed")
        return
    prepare_release(root, plan)
    if args.commit:
        commit_release(root, plan)
    if args.tag:
        print(tag_release(root, plan.version))
    print(
        f"Prepared {summary}"
        + ("" if args.commit else "; review and commit pyproject.toml and CHANGELOG.md")
    )


def main(argv: list[str] | None = None) -> int:
    """Run the release command line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate release metadata and changelog")
    subparsers.add_parser("version", help="print the canonical project version")
    bump_parser = subparsers.add_parser(
        "bump", help="prepare a stable semantic release (files only by default)"
    )
    bump_parser.add_argument("level", choices=("major", "minor", "patch"))
    bump_parser.add_argument(
        "--dry-run", action="store_true", help="validate and describe; change nothing"
    )
    bump_parser.add_argument(
        "--commit", action="store_true", help="commit pyproject.toml and CHANGELOG.md"
    )
    bump_parser.add_argument(
        "--tag",
        action="store_true",
        help="also create the annotated tag (needs --commit)",
    )
    bump_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="skip the clean-worktree check (files only; for hand-off to a committer)",
    )
    bump_parser.add_argument(
        "--date", help="release date, YYYY-MM-DD (default: today UTC)"
    )
    bump_parser.add_argument("--repo-url", help="repository URL for changelog links")
    create_tag_parser = subparsers.add_parser(
        "tag", help="create the annotated tag for the current version on HEAD"
    )
    create_tag_parser.add_argument("--dry-run", action="store_true")
    validate_parser = subparsers.add_parser(
        "validate-tag", help="validate a release tag against project metadata"
    )
    validate_parser.add_argument("tag")
    notes_parser = subparsers.add_parser(
        "notes", help="print the changelog section for a release tag"
    )
    notes_parser.add_argument("tag")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        if args.command == "check":
            print(f"Release metadata valid: {check_repository(root)}")
        elif args.command == "version":
            print(project_version(root))
        elif args.command == "bump":
            _run_bump(root, args)
        elif args.command == "tag":
            print(tag_release(root, check_repository(root), dry_run=args.dry_run))
        elif args.command == "validate-tag":
            print(f"Release tag valid: v{validate_tag(root, args.tag)}")
        else:
            version = validate_tag(root, args.tag)
            print(release_notes(changelog_text(root), version))
    except (ReleaseError, ValueError) as error:
        print(f"release: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
