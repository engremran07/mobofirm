"""``app.version`` is the one version source and every surface agrees with it."""

from __future__ import annotations

import re
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from app import version as version_module
from app.version import BuildInfo, app_version, build_info, clear_cache
from scripts import release

ROOT = Path(__file__).resolve().parents[3]
FAKE_VERSION = "9.8.7"


@contextmanager
def fake_checkout(version: str = FAKE_VERSION, **env: str) -> Iterator[Path]:
    """Point ``app.version`` at a temporary checkout whose pyproject says ``version``."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "mobofirm"\nversion = "{version}"\n', encoding="utf-8"
        )
        clear_cache()
        try:
            with (
                patch.object(version_module, "PROJECT_ROOT", root),
                patch.dict("os.environ", env),
            ):
                yield root
        finally:
            clear_cache()


class VersionSourceTests(SimpleTestCase):
    def tearDown(self):
        clear_cache()

    def test_runtime_version_is_the_pyproject_version(self):
        pyproject = version_module.pyproject_version(ROOT)

        self.assertRegex(pyproject or "", r"^\d+\.\d+\.\d+$")
        self.assertEqual(app_version(), pyproject)
        self.assertEqual(release.project_version(ROOT), pyproject)

    def test_changelog_has_a_release_section_for_the_current_version(self):
        notes = release.release_notes(release.changelog_text(ROOT), app_version())

        self.assertTrue(notes)
        release.check_repository(ROOT)

    def test_a_bumped_pyproject_changes_the_runtime_version(self):
        with fake_checkout():
            self.assertEqual(app_version(), FAKE_VERSION)

    def test_pyproject_of_another_project_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "other"\nversion = "1.2.3"\n', encoding="utf-8"
            )

            self.assertIsNone(version_module.pyproject_version(root))

    def test_installed_metadata_is_used_when_there_is_no_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            clear_cache()
            with (
                patch.object(version_module, "PROJECT_ROOT", Path(directory)),
                patch.object(version_module, "installed_version", return_value="3.2.1"),
            ):
                self.assertEqual(app_version(), "3.2.1")

    def test_missing_version_source_is_an_explicit_error(self):
        from importlib.metadata import PackageNotFoundError

        with tempfile.TemporaryDirectory() as directory:
            clear_cache()
            with (
                patch.object(version_module, "PROJECT_ROOT", Path(directory)),
                patch.object(
                    version_module,
                    "installed_version",
                    side_effect=PackageNotFoundError,
                ),
                self.assertRaisesMessage(
                    RuntimeError, "Application version unavailable"
                ),
            ):
                build_info()


class BuildMetadataTests(SimpleTestCase):
    def tearDown(self):
        clear_cache()

    def test_environment_variables_provide_sha_and_date(self):
        with fake_checkout(APP_GIT_SHA="ABCDEF1234567890", APP_BUILD_DATE="2026-11-06"):
            info = build_info()

        self.assertEqual(
            info,
            BuildInfo(version=FAKE_VERSION, git_sha="abcdef1", build_date="2026-11-06"),
        )
        self.assertEqual(info.full_version, f"{FAKE_VERSION}+abcdef1")

    def test_build_date_accepts_a_timestamp(self):
        with fake_checkout(
            APP_GIT_SHA="abcdef1", APP_BUILD_DATE="2026-11-06T08:30:00Z"
        ):
            self.assertEqual(build_info().build_date, "2026-11-06")

    def test_invalid_metadata_is_ignored_not_fatal(self):
        with fake_checkout(APP_GIT_SHA="not-a-sha", APP_BUILD_DATE="yesterday") as root:
            self.assertFalse((root / ".git").exists())
            info = build_info()

        self.assertEqual(info, BuildInfo(FAKE_VERSION, None, None))
        self.assertEqual(info.full_version, FAKE_VERSION)

    def test_git_head_is_used_in_a_checkout_and_failures_never_raise(self):
        with fake_checkout() as root:
            (root / ".git").mkdir()
            completed = subprocess.CompletedProcess([], 0, "1a2b3c4 2026-11-05\n", "")
            with patch.object(version_module.subprocess, "run", return_value=completed):
                clear_cache()
                head = build_info()
            self.assertEqual((head.git_sha, head.build_date), ("1a2b3c4", "2026-11-05"))

            for error in (
                FileNotFoundError("git"),
                subprocess.TimeoutExpired("git", 3),
                subprocess.CalledProcessError(128, "git"),
            ):
                clear_cache()
                with patch.object(version_module.subprocess, "run", side_effect=error):
                    info = build_info()
                self.assertEqual(info, BuildInfo(FAKE_VERSION, None, None), error)

    def test_environment_values_win_over_git(self):
        with fake_checkout(APP_GIT_SHA="abcdef1", APP_BUILD_DATE="2026-01-02") as root:
            (root / ".git").mkdir()
            completed = subprocess.CompletedProcess([], 0, "1a2b3c4 2026-11-05\n", "")
            with patch.object(version_module.subprocess, "run", return_value=completed):
                clear_cache()
                info = build_info()

        self.assertEqual((info.git_sha, info.build_date), ("abcdef1", "2026-01-02"))


class BuildPipelineWiringTests(SimpleTestCase):
    """The image build captures the same variables ``app.version`` reads."""

    def _text(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_dockerfile_declares_and_exports_the_build_metadata(self):
        dockerfile = self._text("Dockerfile")

        for variable in (version_module.GIT_SHA_ENV, version_module.BUILD_DATE_ENV):
            self.assertIn(f"ARG {variable}", dockerfile)
            self.assertIn(f"{variable}=${variable}", dockerfile)

    def test_compose_and_ci_pass_the_build_arguments(self):
        compose = self._text("docker-compose.yml")
        workflow = self._text(".github/workflows/ci.yml")

        for variable in (version_module.GIT_SHA_ENV, version_module.BUILD_DATE_ENV):
            self.assertIn(f"{variable}: ${{{variable}:-}}", compose)
            self.assertIn(f"--build-arg {variable}=", workflow)
        self.assertIn("scripts/release.py version", workflow)

    def test_the_image_does_not_override_the_release_version(self):
        self.assertNotIn("APP_VERSION", self._text("Dockerfile"))


class SurfacesAgreeTests(TestCase):
    """Footer, meta tag, health, response header and admin all show one version."""

    def tearDown(self):
        clear_cache()

    def _admin_client(self):
        user = get_user_model().objects.create_superuser(
            username="surface-admin", email="surface-admin@example.com"
        )
        self.client.force_login(user)
        return self.client

    def _assert_every_surface(self, expected: str, sha: str | None = None) -> None:
        home = self.client.get(reverse("home"))
        self.assertContains(
            home,
            f'<meta name="application-version" content="{expected}">',
            html=True,
        )
        self.assertContains(home, f'data-app-version="{expected}"')
        self.assertContains(home, f"Version {expected}")
        self.assertEqual(home["X-App-Version"], expected)

        live = self.client.get(reverse("health-live"))
        ready = self.client.get(reverse("health-ready"))
        self.assertEqual(live.json()["version"], expected)
        self.assertEqual(ready.json()["version"], expected)
        self.assertEqual(ready.json()["build"]["git_sha"], sha)
        self.assertEqual(live["X-App-Version"], expected)
        self.assertEqual(
            self.client.get("/definitely-missing/")["X-App-Version"], expected
        )

        dashboard = self._admin_client().get(reverse("admin:index"))
        self.assertContains(dashboard, f"v{expected}")
        self.assertContains(dashboard, f'data-app-version="{expected}"')
        self.assertContains(dashboard, f"Version {expected}")
        if sha:
            self.assertContains(home, f"build {sha}")
            self.assertContains(dashboard, f"Build {sha}")

    def test_current_version_appears_on_every_surface(self):
        clear_cache()
        self._assert_every_surface(app_version(), build_info().git_sha)

    def test_a_pyproject_bump_flows_to_every_surface(self):
        with fake_checkout(APP_GIT_SHA="c0ffee1", APP_BUILD_DATE="2026-11-06"):
            self._assert_every_surface(FAKE_VERSION, "c0ffee1")

    def test_surfaces_follow_the_release_cli_bump(self):
        """Run the real release flow in a fixture repo and read the result back."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "mobofirm"\nversion = "4.5.6"\n', encoding="utf-8"
            )
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something.\n",
                encoding="utf-8",
            )
            plan = release.ReleasePlan("4.5.6", "4.6.0", "2026-11-06", None)
            release.prepare_release(root, plan)
            clear_cache()
            with patch.object(version_module, "PROJECT_ROOT", root):
                self._assert_every_surface("4.6.0", build_info().git_sha)

    def test_admin_header_shows_version_and_environment_badge(self):
        client = self._admin_client()

        production = client.get(reverse("admin:index"))
        self.assertEqual(
            tuple(production.context["environment"]), ("Production", "success")
        )
        self.assertContains(production, 'class="inline-block font-semibold')
        with override_settings(DEBUG=True):
            development = client.get(reverse("admin:index"))
        self.assertEqual(
            tuple(development.context["environment"]), ("Development", "warning")
        )
        with patch.dict("os.environ", {"APP_ENVIRONMENT": "staging"}):
            staging = client.get(reverse("admin:index"))
        self.assertEqual(tuple(staging.context["environment"]), ("Staging", "info"))

    def test_footer_omits_build_details_when_unknown(self):
        with fake_checkout():
            response = self.client.get(reverse("home"))

        self.assertNotContains(response, "&middot; build")
        self.assertRegex(
            response.content.decode(), rf"Version {re.escape(FAKE_VERSION)}\s*</p>"
        )
