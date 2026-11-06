"""The admin palette is anchored to the public design tokens (one token source)."""

from __future__ import annotations

import re
from pathlib import Path

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
        "SITE_URL",
        "SITE_ICON",
        "SITE_FAVICONS",
        "ENVIRONMENT",
        "DASHBOARD_CALLBACK",
    ):
        assert callable(import_string(UNFOLD[key])), key
    assert callable(import_string(UNFOLD["SIDEBAR"]["navigation"]))
    assert callable(import_string(UNFOLD["TABS"]))
