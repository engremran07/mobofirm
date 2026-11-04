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
