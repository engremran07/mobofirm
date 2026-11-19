"""Console asset tag: manifest resolution, loud failure, and the offline guarantee."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Template

from apps.core.templatetags.console import ConsoleAssetsError
from scripts.check_offline_assets import is_remote_asset, scan_file

MANIFEST: dict[str, dict[str, Any]] = {
    "frontend/src/admin/admin.scss": {
        "file": "assets/console-styles-AAA111.css",
        "isEntry": True,
    },
    "frontend/src/admin/main.js": {
        "file": "assets/console-main-BBB222.js",
        "isEntry": True,
        "imports": ["_vendor-CCC333.js"],
        "css": ["assets/console-main-DDD444.css"],
    },
    "_vendor-CCC333.js": {
        "file": "assets/chunks/vendor-CCC333.js",
        "css": ["assets/vendor-EEE555.css"],
    },
    "frontend/src/admin/lazy.js": {
        "file": "assets/chunks/lazy-FFF666.js",
        "isDynamicEntry": True,
    },
}


def _write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _render(settings: Any, manifest_path: Path) -> str:
    settings.CONSOLE_ASSET_MANIFEST = str(manifest_path)
    return Template("{% load console %}{% console_assets %}").render(Context({}))


def test_tag_resolves_hashed_assets_from_manifest(
    settings: Any, tmp_path: Path
) -> None:
    html = _render(settings, _write_manifest(tmp_path / "manifest.json", MANIFEST))

    assert 'href="/static/build/admin/assets/console-styles-AAA111.css"' in html
    for hashed in ("console-main-DDD444.css", "vendor-EEE555.css"):
        assert f"build/admin/assets/{hashed}" in html
    assert 'type="module"' in html
    assert "build/admin/assets/console-main-BBB222.js" in html
    assert "lazy-FFF666" not in html
    assert html.index("<link") < html.index("<script")


def test_tag_emits_no_inline_code_and_no_cdn(settings: Any, tmp_path: Path) -> None:
    html = _render(settings, _write_manifest(tmp_path / "manifest.json", MANIFEST))

    assert "http://" not in html
    assert "https://" not in html
    for line in html.splitlines():
        if line.startswith("<script"):
            assert line.startswith('<script type="module" src="/static/build/admin/')
            assert line.endswith("></script>")
    assert "onload=" not in html


def test_missing_manifest_fails_loudly(settings: Any, tmp_path: Path) -> None:
    with pytest.raises(ConsoleAssetsError, match="npm run build:admin"):
        _render(settings, tmp_path / "absent.json")


@pytest.mark.parametrize(
    "content", ["{not json", "[]", json.dumps({"x": {"file": "a.js"}})]
)
def test_malformed_manifest_fails_loudly(
    settings: Any, tmp_path: Path, content: str
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConsoleAssetsError):
        _render(settings, path)


def test_entry_without_file_or_unknown_import_fails_loudly(
    settings: Any, tmp_path: Path
) -> None:
    bad_import = {
        "main.js": {"file": "assets/m.js", "isEntry": True, "imports": ["_gone.js"]}
    }
    with pytest.raises(ConsoleAssetsError, match="unknown chunk"):
        _render(settings, _write_manifest(tmp_path / "a.json", bad_import))
    no_file = {"main.js": {"isEntry": True}}
    with pytest.raises(ConsoleAssetsError, match="no output file"):
        _render(settings, _write_manifest(tmp_path / "b.json", no_file))


def test_manifest_changes_are_picked_up_without_restart(
    settings: Any, tmp_path: Path
) -> None:
    path = _write_manifest(tmp_path / "manifest.json", MANIFEST)
    assert "console-main-BBB222.js" in _render(settings, path)

    updated = json.loads(json.dumps(MANIFEST))
    updated["frontend/src/admin/main.js"]["file"] = "assets/console-main-NEW999.js"
    _write_manifest(path, updated)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert "console-main-NEW999.js" in _render(settings, path)


@pytest.mark.parametrize(
    "url",
    [
        "https://fonts.googleapis.com/css2?family=Inter",
        "https://cdn.jsdelivr.net/npm/bootstrap/dist/js/bootstrap.js",
        "//unpkg.com/htmx.org",
        "https://example.com/font.woff2",
    ],
)
def test_offline_check_flags_remote_assets(url: str) -> None:
    assert is_remote_asset(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.w3.org/2000/svg",
        "https://alpinejs.dev/plugins/csp",
        "https://htmx.org/docs",
    ],
)
def test_offline_check_allows_namespaces_and_doc_links(url: str) -> None:
    assert not is_remote_asset(url)


def test_offline_check_flags_css_remote_imports(tmp_path: Path) -> None:
    css = tmp_path / "console.css"
    css.write_text(
        "@import url(https://fonts.googleapis.com/css?family=Inter);", encoding="utf-8"
    )
    assert scan_file(css)
    css.write_text(
        ".a{background:url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E)}",
        encoding="utf-8",
    )
    assert not scan_file(css)
