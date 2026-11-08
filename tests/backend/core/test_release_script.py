import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RELEASE_SCRIPT = ROOT / "scripts" / "release.py"


class ReleaseScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "mobofirm"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (self.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Added\n\n"
            "- Add a verified release workflow.\n",
            encoding="utf-8",
        )
        self._git("init", "--quiet")
        self._git("config", "user.name", "Release Test")
        self._git("config", "user.email", "release-test@example.com")
        self._git("add", "pyproject.toml", "CHANGELOG.md")
        self._git("commit", "--quiet", "-m", "Initialize release fixture")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def _run_release(self, *args):
        return subprocess.run(
            [
                sys.executable,
                str(RELEASE_SCRIPT),
                "--root",
                str(self.root),
                *args,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_check_validates_canonical_version_and_changelog(self):
        result = self._run_release("check")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Release metadata valid: 0.1.0", result.stdout)

    def test_bump_archives_notes_and_updates_canonical_version(self):
        result = self._run_release("bump", "minor")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "0.2.0"', (self.root / "pyproject.toml").read_text())
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [Unreleased]\n\n## [0.2.0] - ", changelog)
        self.assertIn("- Add a verified release workflow.", changelog)

        tag_result = self._run_release("validate-tag", "v0.2.0")
        notes_result = self._run_release("notes", "v0.2.0")
        self.assertEqual(tag_result.returncode, 0, tag_result.stderr)
        self.assertEqual(notes_result.returncode, 0, notes_result.stderr)
        self.assertIn("- Add a verified release workflow.", notes_result.stdout)

    def test_bump_rejects_dirty_worktree_without_changing_files(self):
        changelog_path = self.root / "CHANGELOG.md"
        changelog_path.write_text(
            changelog_path.read_text(encoding="utf-8") + "\nUncommitted change.\n",
            encoding="utf-8",
        )

        result = self._run_release("bump", "patch")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("clean Git worktree", result.stderr)
        self.assertIn('version = "0.1.0"', (self.root / "pyproject.toml").read_text())

    def test_bump_requires_categorized_notes(self):
        changelog_path = self.root / "CHANGELOG.md"
        changelog_path.write_text(
            "# Changelog\n\n## [Unreleased]\n\n",
            encoding="utf-8",
        )
        self._git("add", "CHANGELOG.md")
        self._git("commit", "--quiet", "-m", "Clear unreleased notes")

        result = self._run_release("bump", "patch")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("categorized release notes", result.stderr)
        self.assertIn('version = "0.1.0"', (self.root / "pyproject.toml").read_text())

    def test_bump_rejects_an_existing_local_tag(self):
        self._git("tag", "v0.1.1")

        result = self._run_release("bump", "patch")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git tag v0.1.1 already exists", result.stderr)
        self.assertIn('version = "0.1.0"', (self.root / "pyproject.toml").read_text())

    def test_tag_must_match_canonical_version(self):
        result = self._run_release("validate-tag", "v0.2.0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("match the canonical pyproject.toml version", result.stderr)

    def test_version_prints_the_canonical_version(self):
        result = self._run_release("version")

        self.assertEqual(result.stdout.strip(), "0.1.0")

    def test_dry_run_describes_the_release_and_changes_nothing(self):
        result = self._run_release("bump", "minor", "--dry-run", "--date", "2026-11-06")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.1.0 -> 0.2.0 (2026-11-06)", result.stdout)
        self.assertEqual(self._git("status", "--porcelain").stdout, "")

    def test_bump_writes_dated_section_and_compare_links(self):
        args = (
            "--date",
            "2026-11-06",
            "--repo-url",
            "git@github.com:acme/mobofirm.git",
        )

        first = self._run_release("bump", "minor", *args)
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("## [Unreleased]\n\n## [0.2.0] - 2026-11-06\n", changelog)
        self.assertIn(
            "[Unreleased]: https://github.com/acme/mobofirm/compare/v0.2.0...HEAD",
            changelog,
        )
        self.assertIn(
            "[0.2.0]: https://github.com/acme/mobofirm/releases/tag/v0.2.0", changelog
        )
        self.assertEqual(self._run_release("check").returncode, 0)
        # The link block is not part of the release notes.
        notes = self._run_release("notes", "v0.2.0").stdout
        self.assertNotIn("[Unreleased]:", notes)

        # A second release links to the first and keeps every section.
        (self.root / "CHANGELOG.md").write_text(
            changelog.replace(
                "## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n\n- Fix a bug.\n", 1
            ),
            encoding="utf-8",
        )
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", "Release 0.2.0 and a fix")
        second = self._run_release("bump", "patch", *args)
        final = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(
            "[0.2.1]: https://github.com/acme/mobofirm/compare/v0.2.0...v0.2.1", final
        )
        self.assertIn(
            "[Unreleased]: https://github.com/acme/mobofirm/compare/v0.2.1...HEAD",
            final,
        )
        self.assertEqual(final.count("[Unreleased]:"), 1)
        self.assertEqual(self._run_release("check").returncode, 0)

    def test_check_rejects_a_stale_unreleased_link(self):
        self._run_release("bump", "minor", "--repo-url", "https://github.com/a/b")
        path = self.root / "CHANGELOG.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "compare/v0.2.0...HEAD", "compare/v0.1.0...HEAD"
            ),
            encoding="utf-8",
        )

        result = self._run_release("check")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[Unreleased] link must compare v0.2.0...HEAD", result.stderr)

    def test_bump_is_not_repeatable_without_new_notes(self):
        self.assertEqual(self._run_release("bump", "patch").returncode, 0)
        before = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")

        again = self._run_release("bump", "patch", "--allow-dirty")

        self.assertNotEqual(again.returncode, 0)
        self.assertIn("categorized release notes", again.stderr)
        self.assertEqual(
            (self.root / "CHANGELOG.md").read_text(encoding="utf-8"), before
        )

    def test_allow_dirty_prepares_files_but_never_commits(self):
        (self.root / "untracked.txt").write_text("other work", encoding="utf-8")

        result = self._run_release("bump", "minor", "--allow-dirty")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('version = "0.2.0"', (self.root / "pyproject.toml").read_text())
        log = self._git("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(log), 1)

    def test_commit_and_tag_only_happen_with_explicit_flags(self):
        plain = self._run_release("bump", "minor")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(self._git("tag", "--list").stdout.strip(), "")
        self.assertEqual(len(self._git("log", "--oneline").stdout.splitlines()), 1)
        self._git("checkout", "--quiet", "--", ".")

        result = self._run_release("bump", "minor", "--commit", "--tag")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._git("tag", "--list").stdout.strip(), "v0.2.0")
        self.assertEqual(self._git("status", "--porcelain").stdout, "")
        self.assertEqual(
            self._git("log", "-1", "--format=%s").stdout.strip(), "Release v0.2.0"
        )
        self.assertEqual(self._run_release("validate-tag", "v0.2.0").returncode, 0)

    def test_tag_flag_requires_commit(self):
        result = self._run_release("bump", "minor", "--tag")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--tag requires --commit", result.stderr)
        self.assertIn('version = "0.1.0"', (self.root / "pyproject.toml").read_text())

    def test_tag_command_is_idempotent_and_dry_run_safe(self):
        self.assertEqual(self._run_release("bump", "minor").returncode, 0)
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", "Release")

        preview = self._run_release("tag", "--dry-run")
        self.assertEqual(self._git("tag", "--list").stdout.strip(), "")
        created = self._run_release("tag")
        repeated = self._run_release("tag")

        self.assertIn("would create annotated tag v0.2.0", preview.stdout)
        self.assertIn("Created annotated tag v0.2.0", created.stdout)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already exists on HEAD", repeated.stdout)

        (self.root / "later.txt").write_text("later", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", "Later work")
        moved = self._run_release("tag")
        self.assertNotEqual(moved.returncode, 0)
        self.assertIn("different commit", moved.stderr)

    def test_tag_requires_a_changelog_section_for_the_version(self):
        result = self._run_release("tag")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one section for 0.1.0", result.stderr)
