from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class StaticAuditTests(SimpleTestCase):
    def test_reports_duplicate_and_empty_assets_without_deleting_them(self):
        with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
            first = Path(first_dir)
            second = Path(second_dir)
            (first / "duplicate-audit.css").write_text("a{}", encoding="utf-8")
            (second / "duplicate-audit.css").write_text("b{}", encoding="utf-8")
            empty_file = first / "empty-audit.css"
            empty_file.write_text("", encoding="utf-8")
            output = StringIO()
            errors = StringIO()

            with (
                override_settings(
                    STATICFILES_DIRS=[first_dir, second_dir],
                    STATIC_ROOT=str(second / "collected"),
                ),
                self.assertRaises(CommandError),
            ):
                call_command("audit_static", stdout=output, stderr=errors)

            report = errors.getvalue()
            self.assertIn("Duplicate path: duplicate-audit.css", report)
            self.assertIn("Empty file: empty-audit.css", report)
            self.assertTrue(empty_file.exists())
            self.assertIn("Scanned", output.getvalue())

    def test_reports_identical_content_across_distinct_paths(self):
        with TemporaryDirectory() as static_dir:
            first = Path(static_dir, "first.css")
            second = Path(static_dir, "nested", "second.css")
            second.parent.mkdir()
            first.write_text("a{}", encoding="utf-8")
            second.write_text("a{}", encoding="utf-8")
            output = StringIO()
            errors = StringIO()
            with override_settings(STATICFILES_DIRS=[static_dir]):
                call_command(
                    "audit_static",
                    stdout=output,
                    stderr=errors,
                )

            report = errors.getvalue()
            self.assertIn(
                "Duplicate content (review; assets are retained): sha256:", report
            )
            self.assertIn("first.css", report)
            self.assertIn("nested/second.css", report)
            self.assertIn("identical-content group(s)", output.getvalue())
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_report_only_returns_success_for_findings(self):
        with TemporaryDirectory() as static_dir:
            Path(static_dir, "empty-audit.js").write_text("", encoding="utf-8")
            output = StringIO()
            errors = StringIO()
            with override_settings(STATICFILES_DIRS=[static_dir]):
                call_command(
                    "audit_static",
                    "--report-only",
                    stdout=output,
                    stderr=errors,
                )

            self.assertIn("Empty file:", errors.getvalue())
            self.assertIn("Static audit found", output.getvalue())
