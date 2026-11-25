"""Tests for public and staff-console template CSS coverage."""

from pathlib import Path

from scripts.css_coverage import defined_classes


def test_defined_classes_includes_nested_console_stylesheets(tmp_path: Path) -> None:
    """Include classes from both the public and nested console builds."""
    (tmp_path / "site.css").write_text(".public-card { color: red; }", encoding="utf-8")
    admin_assets = tmp_path / "admin" / "assets"
    admin_assets.mkdir(parents=True)
    (admin_assets / "console.css").write_text(
        ".console-panel { color: blue; }", encoding="utf-8"
    )

    assert defined_classes(tmp_path) >= {"public-card", "console-panel"}
