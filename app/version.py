"""The one runtime source for the application version and build metadata.

``pyproject.toml`` ``[project].version`` is the release version (bumped only by
``scripts/release.py``). At runtime the version is read from the source checkout's
``pyproject.toml`` when it is present (an editable install's package metadata is
frozen at install time and goes stale after a bump) and from the installed
distribution metadata otherwise (a wheel/site-packages deployment). Every
surface (footer, admin, ``/health/``, ``X-App-Version``, ``<meta>``) reads this
module; nothing else may hard-code or re-derive the version.

Build metadata is optional and never fails: the git short SHA and build date come
from ``APP_GIT_SHA`` / ``APP_BUILD_DATE`` (set at image build time) and, in a git
checkout, from ``git log`` of ``HEAD`` (so ``build_date`` is the commit date there).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

logger = logging.getLogger(__name__)

DISTRIBUTION = "mobofirm"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHORT_SHA_LENGTH = 7
GIT_SHA_ENV = "APP_GIT_SHA"
BUILD_DATE_ENV = "APP_BUILD_DATE"
ENVIRONMENT_ENV = "APP_ENVIRONMENT"
GIT_TIMEOUT_SECONDS = 3
_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """Release version plus optional build metadata (None when unknown)."""

    version: str
    git_sha: str | None
    build_date: str | None

    @property
    def full_version(self) -> str:
        """SemVer version with the SHA as build metadata (``0.2.0+abc1234``)."""
        return f"{self.version}+{self.git_sha}" if self.git_sha else self.version


def pyproject_version(root: Path | None = None) -> str | None:
    """Return ``[project].version`` of the checkout at ``root``, if it is ours."""
    path = (root or PROJECT_ROOT) / "pyproject.toml"
    try:
        with path.open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = project.get("version")
    if project.get("name") != DISTRIBUTION or not isinstance(value, str):
        return None
    return value


def _resolve_version() -> str:
    from_source = pyproject_version()
    if from_source is not None:
        return from_source
    try:
        return installed_version(DISTRIBUTION)
    except PackageNotFoundError as error:
        raise RuntimeError(
            "Application version unavailable: no pyproject.toml next to the code "
            f"and no installed {DISTRIBUTION!r} distribution"
        ) from error


def _clean_sha(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not _SHA_PATTERN.fullmatch(candidate):
        if candidate:
            logger.warning("Ignoring %s: not a hexadecimal git SHA", GIT_SHA_ENV)
        return None
    return candidate[:SHORT_SHA_LENGTH].lower()


def _clean_date(value: str | None) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        if len(candidate) == len("YYYY-MM-DD"):
            return date.fromisoformat(candidate).isoformat()
        return datetime.fromisoformat(candidate).date().isoformat()
    except ValueError:
        logger.warning("Ignoring %s: not an ISO 8601 date", BUILD_DATE_ENV)
        return None


def _git_head() -> tuple[str | None, str | None]:
    """Short SHA and commit date of HEAD; (None, None) when git is unusable."""
    if not (PROJECT_ROOT / ".git").exists():
        return None, None
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(PROJECT_ROOT),
                "log",
                "-1",
                f"--abbrev={SHORT_SHA_LENGTH}",
                "--format=%h %cs",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    sha, _, commit_date = result.stdout.strip().partition(" ")
    return _clean_sha(sha), _clean_date(commit_date)


@lru_cache(maxsize=1)
def build_info() -> BuildInfo:
    """Version and build metadata for this process (cached; see :func:`clear_cache`)."""
    git_sha = _clean_sha(os.environ.get(GIT_SHA_ENV))
    build_date = _clean_date(os.environ.get(BUILD_DATE_ENV))
    if git_sha is None or build_date is None:
        head_sha, head_date = _git_head()
        git_sha = git_sha or head_sha
        build_date = build_date or head_date
    return BuildInfo(version=_resolve_version(), git_sha=git_sha, build_date=build_date)


def app_version() -> str:
    """The release version (``MAJOR.MINOR.PATCH``)."""
    return build_info().version


def clear_cache() -> None:
    """Forget the cached version/build (tests, or after a version bump)."""
    build_info.cache_clear()


def environment_name() -> str:
    """Deployment label from ``APP_ENVIRONMENT`` (empty when not set)."""
    return os.environ.get(ENVIRONMENT_ENV, "").strip().lower()
