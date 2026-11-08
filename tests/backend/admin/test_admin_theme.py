"""The admin palette is anchored to the public design tokens (one token source)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import unfold
from django.contrib.auth import get_user_model
from django.urls import reverse

from app.settings import unfold as unfold_settings
from app.settings.unfold import UNFOLD

TOKENS = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "styles" / "tokens.css"
)


def _theme_block(css: str, selector: str) -> str:
    start = css.index(selector)
    return css[start : css.index("}", start)]


def _token(block: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", block)
    assert match, f"{name} missing from tokens.css"
    return match.group(1).lower()


def test_brand_colours_match_the_public_design_tokens():
    css = TOKENS.read_text(encoding="utf-8")
    light = _theme_block(css, ':root[data-bs-theme="light"]')
    dark = _theme_block(css, ':root[data-theme="dark"]')

    assert unfold_settings.BRAND_PRIMARY_LIGHT == _token(light, "--mf-accent")
    assert unfold_settings.BRAND_PRIMARY_STRONG_LIGHT == _token(
        light, "--mf-accent-strong"
    )
    assert unfold_settings.BRAND_PRIMARY_DARK == _token(dark, "--mf-accent")
    assert unfold_settings.BRAND_PRIMARY_STRONG_DARK == _token(
        dark, "--mf-accent-strong"
    )


def test_unfold_colour_scale_uses_the_brand_anchors():
    primary = UNFOLD["COLORS"]["primary"]

    assert primary["600"] == unfold_settings.BRAND_PRIMARY_LIGHT
    assert primary["700"] == unfold_settings.BRAND_PRIMARY_STRONG_LIGHT
    assert primary["300"] == unfold_settings.BRAND_PRIMARY_DARK
    assert primary["400"] == unfold_settings.BRAND_PRIMARY_STRONG_DARK
    assert set(primary) == set(UNFOLD["COLORS"]["base"])


def test_unfold_callables_are_importable_dotted_paths():
    from django.utils.module_loading import import_string

    for key in (
        "SITE_TITLE",
        "SITE_HEADER",
        "SITE_SUBHEADER",
        "SITE_VERSION",
        "SITE_URL",
        "SITE_ICON",
        "SITE_FAVICONS",
        "ENVIRONMENT",
        "DASHBOARD_CALLBACK",
    ):
        assert callable(import_string(UNFOLD[key])), key
    for key in ("STYLES", "SCRIPTS"):
        assert UNFOLD[key], key
        assert all(callable(import_string(path)) for path in UNFOLD[key]), key
    assert callable(import_string(UNFOLD["SIDEBAR"]["navigation"]))
    assert callable(import_string(UNFOLD["TABS"]))


# -- visible light / dark / system switcher -----------------------------------------
ROOT = Path(__file__).resolve().parents[3]
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", re.IGNORECASE
)


@pytest.fixture
def admin_page(db, client):
    user = get_user_model().objects.create_superuser(
        username="theme-contract", email="theme-contract@example.test"
    )
    client.force_login(user)
    return client.get(reverse("admin:index"))


def test_admin_theme_is_not_locked_so_the_user_can_switch():
    assert "THEME" not in UNFOLD


def test_header_renders_the_three_way_theme_switcher(admin_page):
    html = admin_page.content.decode()

    assert admin_page.status_code == 200
    assert "data-admin-theme-switch" in html
    for choice in ("light", "dark", "auto"):
        assert f'data-admin-theme-choice="{choice}"' in html
        assert f"switchTheme('{choice}')" in html
    assert html.count('aria-pressed="true"') >= 1
    assert 'role="group" aria-label="Colour theme"' in html


def test_theme_assets_are_same_origin_files_with_no_inline_script(admin_page):
    html = admin_page.content.decode()

    assert 'src="/static/build/admin-theme.js"' in html
    assert 'href="/static/build/admin-theme-switch.css"' in html
    # The synchronous theme script must come before Unfold's app script and Alpine.
    assert html.index("build/admin-theme.js") < html.index("unfold/js/app.js")
    assert INLINE_SCRIPT.findall(html) == []


def test_admin_theme_script_uses_unfolds_persist_key_and_the_public_key():
    script = (ROOT / "frontend" / "src" / "admin-theme.js").read_text(encoding="utf-8")
    app_js = Path(unfold.__file__).parent / "static" / "unfold" / "js" / "app.js"
    assert '.as("adminTheme")' in app_js.read_text(encoding="utf-8")
    assert '"adminTheme"' in script
    assert '"mobofirm-theme"' in script
    public = (ROOT / "frontend" / "src" / "modules" / "theme.js").read_text(
        encoding="utf-8"
    )
    assert 'STORAGE_KEY = "mobofirm-theme"' in public


def test_vite_builds_the_admin_theme_entries():
    config = (ROOT / "vite.config.js").read_text(encoding="utf-8")

    assert "admin-theme.js" in config
    assert "styles/admin-theme.css" in config
