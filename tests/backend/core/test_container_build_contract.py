"""The production image must build from a clean checkout (audit finding F-01).

Docker is not always available where the suite runs, so the contract is checked from
the files: every ``COPY`` source that comes from the build context must contain files
tracked by git (a git-ignored or absent directory such as the generated
``static/build`` would make ``docker build`` fail on a clean checkout), and must not be
excluded by ``.dockerignore``.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def _logical_lines(text: str) -> list[str]:
    """Dockerfile instructions with line continuations joined and comments removed."""
    lines: list[str] = []
    buffer = ""
    in_heredoc = ""
    for raw in text.splitlines():
        if in_heredoc:
            if raw.strip() == in_heredoc:
                in_heredoc = ""
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        buffer += stripped.removesuffix("\\").strip() + " "
        if stripped.endswith("\\"):
            continue
        heredoc = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", buffer)
        if heredoc:
            in_heredoc = heredoc.group(1)
        lines.append(buffer.strip())
        buffer = ""
    return lines


def context_copy_sources() -> list[tuple[str, str]]:
    """(stage, source) for each COPY taken from the build context (not ``--from``)."""
    found: list[tuple[str, str]] = []
    stage = ""
    for line in _logical_lines(DOCKERFILE.read_text(encoding="utf-8")):
        keyword, _, rest = line.partition(" ")
        if keyword.upper() == "FROM":
            stage = rest.split(" AS ")[-1].strip()
            continue
        if keyword.upper() != "COPY":
            continue
        tokens = shlex.split(rest)
        flags = [token for token in tokens if token.startswith("--")]
        paths = [token for token in tokens if not token.startswith("--")]
        if any(flag.startswith("--from=") for flag in flags):
            continue
        found.extend((stage, source.rstrip("/")) for source in paths[:-1])
    return found


def tracked_files(source: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def ignored_by_dockerignore(source: str) -> str | None:
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        pattern = raw.strip()
        if not pattern or pattern.startswith(("#", "!")):
            continue
        pattern = pattern.rstrip("/")
        if source == pattern or source.startswith(f"{pattern}/"):
            return pattern
    return None


class ContainerBuildContractTests(SimpleTestCase):
    def test_dockerfile_declares_context_copy_sources(self):
        sources = {source for _stage, source in context_copy_sources()}

        self.assertTrue(
            {
                "manage.py",
                "pyproject.toml",
                "app",
                "apps",
                "templates",
                "deploy",
                "frontend",
            }
            <= sources,
            sources,
        )

    def test_every_context_copy_source_is_tracked_by_git(self):
        for stage, source in context_copy_sources():
            with self.subTest(stage=stage, source=source):
                self.assertTrue(
                    tracked_files(source),
                    f"COPY source {source!r} (stage {stage!r}) has no git-tracked "
                    "files, so `docker build` fails on a clean checkout.",
                )

    def test_no_context_copy_source_is_excluded_by_dockerignore(self):
        for stage, source in context_copy_sources():
            with self.subTest(stage=stage, source=source):
                self.assertIsNone(
                    ignored_by_dockerignore(source),
                    f"COPY source {source!r} is excluded by .dockerignore.",
                )

    def test_root_static_is_not_copied_from_the_context(self):
        """``static/`` only holds generated Vite output; the frontend stage supplies it."""
        self.assertNotIn("static", {source for _stage, source in context_copy_sources()})
        text = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("--from=frontend", text)
        self.assertIn("/build/static/build ./static/build", text)

    def test_generated_build_output_is_dockerignored_and_git_ignored(self):
        """``static/build`` is generated: the image gets it from the frontend stage."""
        self.assertEqual(ignored_by_dockerignore("static/build"), "static/build")
        self.assertEqual(tracked_files("static/build"), [])
