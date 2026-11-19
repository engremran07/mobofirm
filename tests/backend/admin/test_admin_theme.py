"""Design-token drift guard: one source of truth for the public site and the console.

Every colour lives in ``frontend/src/shared/tokens/_palette.scss`` (see
``tests/backend/design_tokens.py``); both bundles only emit it. These tests fail when

* a colour literal (hex, ``rgb()``/``hsl()``) appears in any first-party stylesheet,
  script, template or Python source outside the token files;
* light and dark stop defining the same token set, or the brand anchors move;
* a text/surface/accent pair drops below WCAG AA in either theme (or high contrast);
* either bundle stops consuming the shared emitters.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from tests.backend import design_tokens as tokens
from tests.backend.admin.theme_guard import mentions_removed_theme

ROOT = tokens.ROOT
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*application/json)([^>]*)>", re.IGNORECASE
)
ADMIN_JS = ROOT / "frontend" / "src" / "admin" / "js"
PUBLIC_THEME_JS = ROOT / "frontend" / "src" / "modules" / "theme.js"
STORAGE_KEY = "mobofirm-theme"

# Brand colours the console contract fixes explicitly.
BRAND = {
    "light": {"accent": "#0b7376", "accent-strong": "#07595d"},
    "dark": {"accent": "#72d9ce", "accent-strong": "#4fc4b7"},
}

# Foreground/background token pairs: (foreground, background, minimum ratio).
TEXT_AA = 4.5
NON_TEXT_AA = 3.0
CONTRAST_PAIRS = [
    ("text", "page", TEXT_AA),
    ("text", "surface", TEXT_AA),
    ("text", "surface-raised", TEXT_AA),
    ("text", "surface-soft", TEXT_AA),
    ("text-muted", "page", TEXT_AA),
    ("text-muted", "surface", TEXT_AA),
    ("text-muted", "surface-raised", TEXT_AA),
    ("accent", "page", TEXT_AA),
    ("accent", "surface", TEXT_AA),
    ("accent", "surface-raised", TEXT_AA),
    ("accent-strong", "surface", TEXT_AA),
    ("link-hover", "surface", TEXT_AA),
    ("accent-ink", "accent", TEXT_AA),
    ("accent-ink", "accent-strong", TEXT_AA),
    ("danger", "page", TEXT_AA),
    ("danger", "surface", TEXT_AA),
    ("danger-ink", "danger", TEXT_AA),
    ("success", "page", TEXT_AA),
    ("success", "surface", TEXT_AA),
    ("warning", "page", TEXT_AA),
    ("warning", "surface", TEXT_AA),
    ("info", "page", TEXT_AA),
    ("info", "surface", TEXT_AA),
    ("sidebar-text", "sidebar-bg", TEXT_AA),
    ("sidebar-heading", "sidebar-bg", TEXT_AA),
    ("focus", "page", NON_TEXT_AA),
    ("focus", "surface", NON_TEXT_AA),
    ("border-strong", "surface", NON_TEXT_AA),
    ("border-strong", "surface-raised", NON_TEXT_AA),
]
# Status ink on its own soft tint (badge/alert/toast): token mixed into the raised surface.
SOFT_TINT = {"accent": 11, "success": 12, "warning": 13, "danger": 11, "info": 11}

# Colour literals allowed outside the token files. Each entry is data that cannot use a
# CSS custom property, with the reason. Keep this list short; never add UI chrome here.
LITERAL_ALLOWLIST = {
    "apps/core/pwa.py": "web-app manifest colours are JSON, not CSS (kept equal to the tokens)",
    "apps/users/services/mfa_qr.py": "QR modules must be pure black on white to scan",
    "apps/site_settings/validators.py": "an example hex in a validation message",
    "apps/devices/templatetags/device_tags.py": "generated brand-logo SVG artwork palette",
    "apps/devices/management/commands/generate_brand_logos.py": "brand-logo artwork palette",
    "apps/firmwares/management/commands/download_brand_logos.py": "brand-logo artwork palette",
    "apps/distribution/video/plan.py": "share-video artwork palette (frames are rendered off-screen to MP4, not UI chrome)",
    "apps/distribution/video/runtime.css": "share-video frame stylesheet (off-screen renderer, fixed dark artwork)",
    "apps/distribution/video/runtime.js": "share-video frame renderer (SVG artwork drawn off-screen)",
}
STYLE_SUFFIXES = {".scss", ".css"}
SCRIPT_SUFFIXES = {".js", ".html", ".htm", ".py", ".svg", ".xsl", ".xml", ".txt"}
SCAN_ROOTS = ("frontend/src", "templates", "apps", "app")
SKIP_PARTS = {"migrations", "node_modules", "__pycache__", "build", "static_collected"}
STYLE_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])")
SCRIPT_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})(?![\w-])")
COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?)\((?!\s*var\()", re.IGNORECASE)


def _first_party_files() -> list[Path]:
    found: list[Path] = []
    for root in SCAN_ROOTS:
        for path in (ROOT / root).rglob("*"):
            if not path.is_file() or SKIP_PARTS & set(path.parts):
                continue
            if path in tokens.TOKEN_FILES:
                continue
            if path.suffix in STYLE_SUFFIXES | SCRIPT_SUFFIXES:
                found.append(path)
    return sorted(found)


def _literals(path: Path) -> list[str]:
    hex_pattern = STYLE_HEX if path.suffix in STYLE_SUFFIXES else SCRIPT_HEX
    hits: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if hex_pattern.search(line) or COLOUR_FUNCTION.search(line):
            hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:90]}")
    return hits


def test_no_colour_literal_exists_outside_the_token_files():
    offenders = [
        hit
        for path in _first_party_files()
        if path.relative_to(ROOT).as_posix() not in LITERAL_ALLOWLIST
        for hit in _literals(path)
    ]

    assert offenders == [], (
        "colour literals outside frontend/src/shared/tokens/ (use a --mf-* token):\n"
        + "\n".join(offenders[:40])
    )


def test_literal_allowlist_entries_are_real_and_still_needed():
    for relative, reason in LITERAL_ALLOWLIST.items():
        path = ROOT / relative
        assert reason.strip()
        assert path.is_file(), f"{relative} no longer exists; drop it from LITERAL_ALLOWLIST"
        assert _literals(path), f"{relative} has no colour literal; drop it from LITERAL_ALLOWLIST"


def test_pwa_manifest_defaults_stay_equal_to_the_tokens():
    from apps.core import pwa

    light = tokens.themes()["light"]
    assert pwa.DEFAULT_THEME_COLOR.lower() == light["accent"]
    assert pwa.DEFAULT_BACKGROUND_COLOR.lower() == light["page"]


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_palette_parses_and_uses_six_digit_hex(mode):
    palette = tokens.themes()[mode]

    assert len(palette) >= 25
    assert all(tokens.HEX_VALUE.match(value) for value in palette.values())


def test_light_and_dark_define_the_same_token_set():
    palettes = tokens.themes()

    assert set(palettes["light"]) == set(palettes["dark"])
    for variant in ("light-high", "dark-high"):
        assert set(palettes[variant]) == set(palettes[variant.split("-")[0]])


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_brand_accent_anchors_match_the_contract(mode):
    palette = tokens.themes()[mode]
    for name, expected in BRAND[mode].items():
        assert palette[name] == expected


@pytest.mark.parametrize("mode", ["light", "dark", "light-high", "dark-high"])
def test_token_pairs_meet_wcag_aa_contrast(mode):
    palette = tokens.themes()[mode]
    # High contrast must clear AA as well; the sidebar chrome is not themed separately there.
    failures = {
        f"{fg} on {bg}": round(tokens.contrast(palette[fg], palette[bg]), 2)
        for fg, bg, minimum in CONTRAST_PAIRS
        if tokens.contrast(palette[fg], palette[bg]) < minimum
    }

    assert failures == {}, f"{mode} contrast failures: {failures}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_status_ink_is_legible_on_its_soft_tint(mode):
    palette = tokens.themes()[mode]
    failures = {}
    for name, percent in SOFT_TINT.items():
        tint = tokens.mix(palette[name], percent, palette["surface-raised"])
        ratio = tokens.contrast(palette[name], tint)
        if ratio < TEXT_AA:
            failures[f"{name} on its soft tint"] = round(ratio, 2)

    assert failures == {}, f"{mode} soft-tint contrast failures: {failures}"


def test_both_bundles_consume_the_shared_token_emitters():
    for source in (tokens.PUBLIC_TOKENS, tokens.CONSOLE_TOKENS):
        text = source.read_text(encoding="utf-8")
        assert "shared/tokens/emit" in text, f"{source.name} must use the shared emitters"
        assert "emit.theme-tokens" in text and "emit.scale-tokens" in text
        assert not re.search(r"--mf-[a-z-]+:\s*#", text), f"{source.name} defines its own colour"


def test_token_emitter_defines_light_dark_and_system_themes():
    emit = tokens.EMIT.read_text(encoding="utf-8")

    for needle in (
        '[data-bs-theme="dark"]',
        "prefers-color-scheme: dark",
        ":root:not([data-bs-theme])",
        "prefers-reduced-motion: reduce",
        "color-scheme: light",
        "color-scheme: dark",
        'data-contrast="high"',
    ):
        assert needle in emit, needle
    # The console bundle turns the system theme on; the public site resolves it in JS.
    assert "theme-tokens($system: false" in tokens.PUBLIC_TOKENS.read_text(encoding="utf-8")
    assert "@include emit.theme-tokens;" in tokens.CONSOLE_TOKENS.read_text(encoding="utf-8")


def test_console_bootstrap_seam_takes_its_colours_from_the_palette():
    seam = (ROOT / "frontend" / "src" / "admin" / "styles" / "_bootstrap.scss").read_text(
        encoding="utf-8"
    )

    assert "shared/tokens/palette" in seam
    assert not tokens.HEX_VALUE.search(seam)


def test_console_theme_script_shares_the_public_storage_key():
    public = PUBLIC_THEME_JS.read_text(encoding="utf-8")
    assert f'STORAGE_KEY = "{STORAGE_KEY}"' in public

    console_sources = {
        path: path.read_text(encoding="utf-8") for path in ADMIN_JS.rglob("*.js")
    }
    owners = [path for path, text in console_sources.items() if STORAGE_KEY in text]
    assert owners, f"no console script reads the shared '{STORAGE_KEY}' preference"


@pytest.fixture(name="admin_page")
def admin_page_fixture(django_user_model, client):
    user = django_user_model.objects.create_superuser(
        username="theme-contract", email="theme-contract@example.test"
    )
    client.force_login(user)
    return client.get(reverse("admin:index"))


def test_console_pages_load_the_console_bundle_not_a_third_party_theme(admin_page):
    html = admin_page.content.decode()

    assert admin_page.status_code == 200
    assert "/static/build/admin/" in html
    assert not mentions_removed_theme(html)
    assert re.search(r"<html[^>]*\blang=", html)


def test_header_renders_the_theme_switcher_for_all_three_modes(admin_page):
    html = admin_page.content.decode()

    for choice in ("light", "dark", "auto"):
        assert re.search(rf'data-console-theme-set="{choice}"', html), choice
    # The pressed state is server-rendered (JS only keeps it in sync).
    assert 'aria-pressed="' in html
    assert re.search(r'role="group"[^>]*aria-label="[^"]*[Tt]heme', html)


def test_theme_assets_are_same_origin_files_with_no_inline_script(admin_page):
    html = admin_page.content.decode()

    assert INLINE_SCRIPT.findall(html) == []
    scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html)
    assert scripts, "the console page loads no script"
    assert all(src.startswith("/static/") for src in scripts), scripts
    styles = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', html)
    styles += re.findall(r'<link[^>]*href="([^"]+)"[^>]*rel="stylesheet"', html)
    assert styles
    assert all(href.startswith("/static/") for href in styles), styles


def test_first_paint_script_runs_before_the_console_module(admin_page):
    html = admin_page.content.decode()

    init = html.index("build/theme-init")
    module = html.index("build/admin/assets/console-main")
    assert init < module
    init_tag = html[html.rfind("<script", 0, init) : html.index(">", init)]
    assert "module" not in init_tag
    assert "defer" not in init_tag and "async" not in init_tag


def test_vite_builds_the_console_entries_with_a_manifest():
    config = (ROOT / "vite.config.js").read_text(encoding="utf-8")

    assert "frontend/src/admin/main.js" in config
    assert "frontend/src/admin/admin.scss" in config
    assert "frontend/src/styles/tokens.scss" in config
    assert "manifest: true" in config
    assert "[hash]" in config
    # The old admin-only theme bundle no longer exists.
    assert "admin-theme" not in config
    assert not (ROOT / "frontend" / "src" / "admin-theme.js").exists()
    assert not (ROOT / "frontend" / "src" / "styles" / "admin-theme.css").exists()
