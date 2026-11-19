"""What this machine can do for motion video, reported honestly.

The renderer needs three free, local tools: ``ffmpeg`` (H.264/AAC encode, loudness),
headless Chromium driven by Playwright (draws every frame from an HTML/SVG motion
template) and Python's standard library (procedural music). Offline text-to-speech is
optional (``espeak-ng`` or ``piper``); without it the video has captions and music only.
Nothing here calls a network service.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

ENV_FFMPEG = "DISTRIBUTION_FFMPEG"
ENV_ESPEAK = "DISTRIBUTION_ESPEAK"
ENV_PIPER = "DISTRIBUTION_PIPER"
ENV_PIPER_MODEL = "DISTRIBUTION_PIPER_MODEL"
FONT_CANDIDATES = (
    "Segoe UI",
    "Inter",
    "Roboto",
    "Helvetica Neue",
    "Arial",
    "DejaVu Sans",
    "Liberation Sans",
)
_FONT_FILES = {
    "Segoe UI": ("segoeui.ttf", "SegoeUI-Regular.ttf"),
    "Inter": ("Inter-Regular.ttf", "Inter.ttf"),
    "Roboto": ("Roboto-Regular.ttf", "Roboto.ttf"),
    "Helvetica Neue": ("HelveticaNeue.ttc",),
    "Arial": ("arial.ttf", "Arial.ttf"),
    "DejaVu Sans": ("DejaVuSans.ttf",),
    "Liberation Sans": ("LiberationSans-Regular.ttf",),
}
INSTALL_FFMPEG = (
    "Windows: winget install Gyan.FFmpeg --scope user (no administrator needed). "
    "Linux: apt install ffmpeg. macOS: brew install ffmpeg. "
    f"Or set {ENV_FFMPEG} to the ffmpeg executable."
)
INSTALL_CHROMIUM = "Install the video extra (pip install -e .[video]) and run: playwright install chromium"
INSTALL_TTS = (
    "Optional voice-over, offline only: install espeak-ng (Windows: winget install "
    "eSpeak-NG.eSpeak-NG; Linux: apt install espeak-ng) or Piper with a voice model "
    f"({ENV_PIPER}, {ENV_PIPER_MODEL}). Without it videos have captions and music."
)


@dataclass(frozen=True, slots=True)
class Capability:
    key: str
    label: str
    ok: bool
    required: bool
    detail: str
    guidance: str = ""


def _first_existing(patterns: list[str]) -> str:
    for pattern in patterns:
        for found in sorted(glob.glob(pattern, recursive=True)):
            if Path(found).is_file():
                return found
    return ""


def _winget_glob(program: str) -> list[str]:
    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return []
    return [
        str(
            Path(base)
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "Gyan.FFmpeg*"
            / "**"
            / program
        ),
        str(Path(base) / "Microsoft" / "WinGet" / "Links" / program),
    ]


def ffmpeg_path() -> str:
    """The ffmpeg executable: env override, ``PATH``, then a user-level winget install."""
    configured = os.environ.get(ENV_FFMPEG, "")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    if sys.platform == "win32":
        return _first_existing(_winget_glob("ffmpeg.exe"))
    return ""


def espeak_path() -> str:
    configured = os.environ.get(ENV_ESPEAK, "")
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("espeak-ng") or shutil.which("espeak") or ""


def piper_path() -> tuple[str, str]:
    """``(binary, model)`` of a local Piper install, or empty strings."""
    binary = os.environ.get(ENV_PIPER, "") or shutil.which("piper") or ""
    model = os.environ.get(ENV_PIPER_MODEL, "")
    if binary and model and Path(model).is_file():
        return binary, model
    return "", ""


def tts_engine() -> str:
    """``piper``, ``espeak`` or ``''`` (no voice-over)."""
    if piper_path()[0]:
        return "piper"
    return "espeak" if espeak_path() else ""


def chromium_ready() -> tuple[bool, str]:
    if find_spec("playwright") is None:
        return False, "The Playwright package is not installed."
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            path = playwright.chromium.executable_path
    except (ImportError, OSError, RuntimeError, ValueError, PlaywrightError) as error:
        return False, f"Playwright could not start: {error}"[:200]
    if path and Path(path).exists():
        return True, path
    return False, "Chromium is not downloaded for Playwright."


def _has_font(root: Path, name: str) -> bool:
    if not root.exists():
        return False
    if (root / name).exists():
        return True
    return sys.platform != "win32" and any(root.rglob(name))


def fonts_found() -> list[str]:
    roots = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    roots += [
        Path(p)
        for p in ("/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts")
    ]
    return [
        family
        for family, files in _FONT_FILES.items()
        if any(_has_font(root, name) for root in roots for name in files)
    ]


def report() -> list[Capability]:
    ffmpeg = ffmpeg_path()
    chromium_ok, chromium_detail = chromium_ready()
    engine = tts_engine()
    fonts = fonts_found()
    return [
        Capability(
            "ffmpeg",
            "ffmpeg (encode, loudness)",
            bool(ffmpeg),
            True,
            ffmpeg or "Not found.",
            "" if ffmpeg else INSTALL_FFMPEG,
        ),
        Capability(
            "chromium",
            "Headless Chromium (frame renderer)",
            chromium_ok,
            True,
            chromium_detail,
            "" if chromium_ok else INSTALL_CHROMIUM,
        ),
        Capability(
            "tts",
            "Offline voice-over",
            bool(engine),
            False,
            {"piper": "Piper", "espeak": "espeak-ng"}.get(
                engine, "Not installed (captions and music only)."
            ),
            "" if engine else INSTALL_TTS,
        ),
        Capability(
            "fonts",
            "Fonts",
            bool(fonts),
            False,
            ", ".join(fonts)
            if fonts
            else "No preferred font found; a generic sans-serif is used.",
            ""
            if fonts
            else "Install a sans-serif such as Inter or DejaVu Sans for consistent text.",
        ),
    ]


def can_render() -> bool:
    """True when every required tool is present."""
    return all(item.ok for item in report() if item.required)
