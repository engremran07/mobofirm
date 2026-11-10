"""Progressive Web App support: web app manifest, versioned service worker, kill switch.

The design is deliberately small and safe by construction:

* The service worker only ever *stores* two kinds of response: the generated Vite
  build assets (``/static/build/...``) and the brand icons, plus one public offline
  fallback page. It never stores a navigation response, an API response, a download,
  an authenticated page or anything returned to a non-``GET`` request, so no private
  data can reach a Cache Storage entry.
* The worker is generated per request from :func:`worker_config` and the canonical
  source ``apps/core/assets/service_worker.js``. Its cache name embeds a version
  derived from the release version, the build metadata and the precached files'
  modification times, so a deploy always installs a new worker and activation deletes
  every older cache.
* ``PWA_ENABLED = False`` (environment ``PWA_ENABLED=false``) is the kill switch:
  the manifest and the offline page answer 404 and ``/sw.js`` serves a worker that
  unregisters itself and deletes every cache this feature created.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse

from app.version import build_info
from apps.site_settings.services import SiteSettingsSnapshot, get_site_settings

CACHE_PREFIX = "mobofirm-static-"
MANIFEST_CONTENT_TYPE = "application/manifest+json"
WORKER_SOURCE = Path(__file__).resolve().parent / "assets" / "service_worker.js"
KILL_SWITCH_SOURCE = Path(__file__).resolve().parent / "assets" / "service_worker_off.js"

# Same-origin static paths the worker may store (generated build output and icons).
STATIC_BUILD_PREFIX = "build/"
STATIC_ICON_PREFIX = "core/pwa/"
# Build entries every page needs; only files that exist are listed, so a checkout
# without a Vite build still installs (offline page and icons only).
PRECACHE_BUILD_FILES = ("tokens.css", "site.css", "theme-init.js", "site.js")
ICON_FILES = ("icon-192.png", "icon-512.png", "icon-maskable-512.png")
# Navigations the worker never answers (no offline page, no interception at all):
# private areas, downloads and APIs keep the browser's own behaviour.
NEVER_HANDLE_PREFIXES = (
    "/admin/",
    "/accounts/",
    "/users/",
    "/devices/",
    "/files/",
    "/wallet/",
    "/shop/",
    "/marketplace/",
    "/storage/",
    "/seo/",
    "/consent/",
    "/go/",
    "/health/",
    "/firmwares/serve/",
    "/firmwares/file/",
    "/firmwares/api/",
    "/blog/api/",
    "/site/",
    "/static/",
    "/media/",
)
DEFAULT_THEME_COLOR = "#0b7376"
DEFAULT_BACKGROUND_COLOR = "#f5f2eb"
SHORT_NAME_LIMIT = 12


def pwa_enabled() -> bool:
    return bool(getattr(settings, "PWA_ENABLED", True))


def _static_url(path: str) -> str:
    return static(path)


def _static_exists(path: str) -> bool:
    return bool(finders.find(path))


def _static_stamp(path: str) -> str:
    found = finders.find(path)
    if not found:
        return ""
    candidate = Path(found if isinstance(found, str) else found[0])
    try:
        return f"{path}:{candidate.stat().st_mtime_ns}"
    except OSError:
        return ""


def precache_paths() -> list[str]:
    """Static paths (relative to ``STATIC_URL``) installed with the worker."""
    candidates = [f"{STATIC_BUILD_PREFIX}{name}" for name in PRECACHE_BUILD_FILES]
    candidates += [f"{STATIC_ICON_PREFIX}{name}" for name in ICON_FILES]
    return [path for path in candidates if _static_exists(path)]


def offline_url() -> str:
    return reverse("pwa_offline")


def worker_version(paths: list[str]) -> str:
    """Stable fingerprint: release, build metadata and every precached file's mtime."""
    info = build_info()
    material = "|".join(
        [info.full_version, info.build_date or "", *map(_static_stamp, paths)]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def worker_config() -> dict[str, Any]:
    paths = precache_paths()
    return {
        "version": worker_version(paths),
        "cachePrefix": CACHE_PREFIX,
        "offlineUrl": offline_url(),
        "precache": [_static_url(path) for path in paths],
        "staticPrefixes": [
            _static_url(STATIC_BUILD_PREFIX),
            _static_url(STATIC_ICON_PREFIX),
        ],
        "neverHandle": list(NEVER_HANDLE_PREFIXES),
    }


@lru_cache(maxsize=2)
def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_worker() -> tuple[str, str]:
    """(script, version): the live worker, or the self-removing one when disabled."""
    if not pwa_enabled():
        return _source(KILL_SWITCH_SOURCE).replace(
            "__CACHE_PREFIX__", json.dumps(CACHE_PREFIX)
        ), "off"
    config = worker_config()
    script = f"self.MF_PWA = {json.dumps(config, sort_keys=True)};\n" + _source(
        WORKER_SOURCE
    )
    return script, str(config["version"])


def _short_name(name: str) -> str:
    return name if len(name) <= SHORT_NAME_LIMIT else name[:SHORT_NAME_LIMIT].rstrip()


def _theme_color(snapshot: SiteSettingsSnapshot) -> str:
    return snapshot.primary_color or DEFAULT_THEME_COLOR


def _shortcut(name: str, url_name: str) -> dict[str, str] | None:
    try:
        return {"name": name, "url": reverse(url_name)}
    except NoReverseMatch:
        return None


def build_manifest() -> dict[str, Any]:
    """The web app manifest for the current site settings."""
    snapshot = get_site_settings()
    icons: list[dict[str, str]] = [
        {
            "src": _static_url(f"{STATIC_ICON_PREFIX}icon-192.png"),
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": _static_url(f"{STATIC_ICON_PREFIX}icon-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": _static_url(f"{STATIC_ICON_PREFIX}icon-maskable-512.png"),
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]
    shortcuts = [
        item
        for item in (
            _shortcut("Firmware catalogue", "firmwares:browse"),
            _shortcut("Search", "search:results"),
        )
        if item
    ]
    manifest: dict[str, Any] = {
        "id": "/",
        "name": snapshot.site_name,
        "short_name": _short_name(snapshot.site_name),
        "description": snapshot.site_description or snapshot.site_name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": DEFAULT_BACKGROUND_COLOR,
        "theme_color": _theme_color(snapshot),
        "lang": snapshot.default_language,
        "icons": icons,
    }
    if shortcuts:
        manifest["shortcuts"] = shortcuts
    return manifest


@dataclass(frozen=True, slots=True)
class HeadLinks:
    """What ``site_settings/head.html`` needs to advertise the app."""

    manifest_url: str
    worker_url: str
    theme_color: str
    touch_icon_url: str


def head_links() -> HeadLinks | None:
    if not pwa_enabled():
        return None
    return HeadLinks(
        manifest_url=reverse("pwa_manifest"),
        worker_url=reverse("pwa_worker"),
        theme_color=_theme_color(get_site_settings()),
        touch_icon_url=_static_url(f"{STATIC_ICON_PREFIX}icon-192.png"),
    )
