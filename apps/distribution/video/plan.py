"""The video plan: presets, safe zones, timing and validation, as plain data.

A template pack turns content into a ``Draft`` (narration beats plus scenes). ``build_plan``
times it (from estimated or measured speech durations), enforces the 15 to 45 second rule,
and returns a JSON-able dictionary that the browser runtime draws. The plan carries data
only: it is applied with ``textContent`` in the page and embedded with every character that
could end a script block escaped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FPS = 30
MIN_MS = 15_000
MAX_MS = 45_000
OVERLAP_MS = 250
ENTRY_MS = 320
TAIL_MS = 350
MAX_TITLE = 140
MAX_BEAT = 150
SCENE_KINDS = frozenset(
    {"hook", "device", "facts", "points", "steps", "warning", "chart", "list", "cta"}
)
ICONS = frozenset(
    {"usb", "phone", "bolt", "download", "check", "warn", "chip", "power", "clock"}
)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
RUNTIME_DIR = Path(__file__).parent


class PlanError(ValueError):
    """The template could not produce a valid video for this content."""


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    label: str
    width: int
    height: int
    scale: float
    # Fractions of the frame kept free of text: top, right, bottom, left (platform UI).
    safe: tuple[float, float, float, float]


PRESETS: dict[str, Preset] = {
    "9x16": Preset(
        "9x16",
        "Vertical 9:16 (TikTok, Shorts, Reels)",
        1080,
        1920,
        1.0,
        (0.10, 0.13, 0.23, 0.07),
    ),
    "1x1": Preset(
        "1x1", "Square 1:1 (feed)", 1080, 1080, 0.82, (0.07, 0.07, 0.19, 0.07)
    ),
    "16x9": Preset(
        "16x9", "Landscape 16:9 (YouTube)", 1920, 1080, 0.78, (0.07, 0.07, 0.19, 0.07)
    ),
}

# Mirrors the console dark palette (frontend/src/shared/tokens/_palette.scss); a test keeps
# the accent values in step with that file.
BRAND_COLORS = {
    "bg1": "#06161a",
    "bg2": "#0d3a40",
    "accent": "#72d9ce",
    "accent2": "#a8a9eb",
    "text": "#f5f7f6",
    "muted": "#b7c6c4",
    "ok": "#72d9ce",
    "warn": "#f2b24c",
}


@dataclass(frozen=True, slots=True)
class Brand:
    name: str
    site: str
    colors: Mapping[str, str] = field(default_factory=lambda: dict(BRAND_COLORS))

    def validate(self) -> None:
        for key, value in self.colors.items():
            if not _HEX.match(value):
                raise PlanError(f"Brand colour {key} must be #rrggbb.")


@dataclass(frozen=True, slots=True)
class Beat:
    id: str
    text: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class SceneSpec:
    id: str
    kind: str
    data: dict[str, Any]
    beats: tuple[str, ...] = ()
    hold_ms: int = 0


@dataclass(frozen=True, slots=True)
class Draft:
    """What a template pack returns: narration and scenes, not yet timed."""

    beats: tuple[Beat, ...]
    scenes: tuple[SceneSpec, ...]
    seed: int = 1


def estimate_ms(text: str) -> int:
    """Speaking time of ``text`` without a voice (about 2.7 words per second)."""
    words = len(text.split())
    return max(1800, int(words * 370) + 500)


def _timeline(
    draft: Draft,
    beat_ms: Mapping[str, int],
    dropped: frozenset[str],
    pad: int,
    scale: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    beats_by_id = {b.id: b for b in draft.beats}
    beats_out: list[dict[str, Any]] = []
    scenes_out: list[dict[str, Any]] = []
    cursor = 0
    for spec in draft.scenes:
        start = max(0, cursor - (OVERLAP_MS if scenes_out else 0))
        at = start + ENTRY_MS
        scene_beats: list[dict[str, Any]] = []
        for beat_id in spec.beats:
            if beat_id in dropped:
                continue
            beat = beats_by_id[beat_id]
            length = int(beat_ms.get(beat_id, estimate_ms(beat.text)) * scale)
            entry = {"id": beat.id, "start": at, "end": at + length, "text": beat.text}
            beats_out.append(entry)
            scene_beats.append(entry)
            at += length
        end = max(at + TAIL_MS, start + int(spec.hold_ms * scale)) + pad
        data = dict(spec.data)
        if data.pop("sync", False):
            # Items appear on the beat that narrates them (milliseconds into the scene).
            data["at"] = [b["start"] - start for b in scene_beats]
        scenes_out.append(
            {"id": spec.id, "kind": spec.kind, "start": start, "end": end, "data": data}
        )
        cursor = end
    return beats_out, scenes_out, cursor + TAIL_MS


def build_plan(
    draft: Draft,
    *,
    preset: str,
    brand: Brand,
    beat_ms: Mapping[str, int] | None = None,
    min_ms: int = MIN_MS,
    max_ms: int = MAX_MS,
) -> dict[str, Any]:
    """Time and validate ``draft``; the result is the page's only input."""
    if preset not in PRESETS:
        raise PlanError(f"Unknown preset {preset!r}.")
    brand.validate()
    spec = PRESETS[preset]
    if not draft.scenes:
        raise PlanError("A video needs at least one scene.")
    ids = [b.id for b in draft.beats]
    if len(ids) != len(set(ids)):
        raise PlanError("Beat ids must be unique.")
    known = set(ids)
    for scene in draft.scenes:
        if scene.kind not in SCENE_KINDS:
            raise PlanError(f"Unknown scene kind {scene.kind!r}.")
        if not set(scene.beats) <= known:
            raise PlanError(f"Scene {scene.id} refers to an unknown beat.")
    for beat in draft.beats:
        if not beat.text.strip() or len(beat.text) > MAX_BEAT:
            raise PlanError(f"Beat {beat.id} needs 1 to {MAX_BEAT} characters.")
    measured = dict(beat_ms or {})
    dropped: set[str] = set()
    scale, pad = 1.0, 0
    beats_out, scenes_out, total = _timeline(
        draft, measured, frozenset(dropped), pad, scale
    )
    optional = [b.id for b in reversed(draft.beats) if b.optional]
    while total > max_ms and optional:
        dropped.add(optional.pop(0))
        beats_out, scenes_out, total = _timeline(
            draft, measured, frozenset(dropped), pad, scale
        )
    if total > max_ms:
        scale = max(0.72, max_ms / total)
        beats_out, scenes_out, total = _timeline(
            draft, measured, frozenset(dropped), pad, scale
        )
    if total > max_ms:
        raise PlanError(
            f"The video would last {total // 1000} s; the limit is {max_ms // 1000} s."
        )
    if total < min_ms:
        pad = (min_ms - total) // len(draft.scenes) + 1
        beats_out, scenes_out, total = _timeline(
            draft, measured, frozenset(dropped), pad, scale
        )
    if total < min_ms:
        raise PlanError(
            f"The video would last {total // 1000} s; at least {min_ms // 1000} s is required."
        )
    top, right, bottom, left = spec.safe
    return {
        "version": 1,
        "preset": preset,
        "w": spec.width,
        "h": spec.height,
        "u": spec.scale,
        "fps": FPS,
        "total": total,
        "seed": draft.seed,
        "safe": {
            "t": round(top * spec.height),
            "r": round(right * spec.width),
            "b": round(bottom * spec.height),
            "l": round(left * spec.width),
        },
        "brand": {
            "name": brand.name[:40],
            "site": brand.site[:80],
            "colors": dict(brand.colors),
        },
        "beats": beats_out,
        "scenes": scenes_out,
    }


_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}


def plan_json(plan: Mapping[str, Any]) -> str:
    """The plan as JSON that cannot close a ``<script>`` block or inject markup."""
    return json.dumps(plan, ensure_ascii=False, separators=(",", ":")).translate(
        _ESCAPES
    )


CSP = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "script-src 'unsafe-inline'; connect-src 'none'; font-src 'none'; media-src 'none'"
)


def page_html(plan: Mapping[str, Any]) -> str:
    """The self-contained render page: no network, no external file, plan as data."""
    css = (RUNTIME_DIR / "runtime.css").read_text(encoding="utf-8")
    runtime = (RUNTIME_DIR / "runtime.js").read_text(encoding="utf-8")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        f'<style>{css}</style></head><body><div id="stage"></div>'
        f'<script id="plan" type="application/json">{plan_json(plan)}</script>'
        f"<script>{runtime}</script></body></html>"
    )
