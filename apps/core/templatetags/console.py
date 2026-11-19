"""Template helpers for the staff console (``templates/admin`` and ``templates/console``).

Asset part (toolchain stream): ``{% console_assets %}`` renders the content-hashed CSS and
JS tags of the Vite admin build, resolved from ``static/build/admin/.vite/manifest.json``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final, cast

from django import template
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

register = template.Library()

CONSOLE_BUILD_PREFIX: Final = "build/admin/"
_MANIFEST_RELATIVE: Final = (
    Path("static") / "build" / "admin" / ".vite" / "manifest.json"
)
_manifest_cache: dict[Path, tuple[int, dict[str, dict[str, Any]]]] = {}


class ConsoleAssetsError(ImproperlyConfigured):
    """The console build output is missing or malformed; rendering must fail loudly."""


def _manifest_path() -> Path:
    override = getattr(settings, "CONSOLE_ASSET_MANIFEST", None)
    return Path(override) if override else Path(settings.BASE_DIR) / _MANIFEST_RELATIVE


def load_console_manifest() -> dict[str, dict[str, Any]]:
    """Return the parsed Vite manifest, re-read whenever the file changes."""
    path = _manifest_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError as error:
        raise ConsoleAssetsError(
            f"Console asset manifest not found at {path}. Run `npm run build:admin`."
        ) from error
    cached = _manifest_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConsoleAssetsError(
            f"Console asset manifest {path} is unreadable: {error}"
        ) from error
    if not isinstance(parsed, dict) or not any(
        isinstance(item, dict) and item.get("isEntry") for item in parsed.values()
    ):
        raise ConsoleAssetsError(f"Console asset manifest {path} has no entry chunks.")
    manifest = cast("dict[str, dict[str, Any]]", parsed)
    _manifest_cache[path] = (mtime, manifest)
    return manifest


def _chunk_css(
    manifest: dict[str, dict[str, Any]], key: str, seen: set[str]
) -> Iterable[str]:
    """Yield the CSS files of a chunk and of every chunk it statically imports."""
    if key in seen:
        return
    seen.add(key)
    chunk = manifest.get(key)
    if chunk is None:
        raise ConsoleAssetsError(
            f"Console asset manifest references unknown chunk {key!r}."
        )
    for imported in chunk.get("imports", []):
        yield from _chunk_css(manifest, imported, seen)
    yield from chunk.get("css", [])


def console_asset_urls() -> tuple[list[str], list[str]]:
    """Return ``(stylesheet_urls, script_urls)`` for every entry of the console build."""
    manifest = load_console_manifest()
    styles: list[str] = []
    scripts: list[str] = []
    seen: set[str] = set()
    for key, chunk in manifest.items():
        if not chunk.get("isEntry"):
            continue
        file = chunk.get("file")
        if not isinstance(file, str) or not file:
            raise ConsoleAssetsError(
                f"Console asset manifest entry {key!r} has no output file."
            )
        if file.endswith(".css"):
            styles.append(file)
        else:
            scripts.append(file)
        styles.extend(_chunk_css(manifest, key, seen))
    unique_styles = list(dict.fromkeys(styles))
    return (
        [static(f"{CONSOLE_BUILD_PREFIX}{name}") for name in unique_styles],
        [static(f"{CONSOLE_BUILD_PREFIX}{name}") for name in dict.fromkeys(scripts)],
    )


@register.simple_tag
def console_assets() -> SafeString:
    """Render the console stylesheet and module-script tags; no inline code, no CDN."""
    styles, scripts = console_asset_urls()
    tags = [format_html('<link rel="stylesheet" href="{}">', url) for url in styles]
    tags.extend(
        format_html('<script type="module" src="{}" defer></script>', url)
        for url in scripts
    )
    return format_html_join("\n", "{}", ((tag,) for tag in tags))


# --- helpers (S3) ---
