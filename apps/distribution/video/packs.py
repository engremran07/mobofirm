"""Template packs: data-driven, versioned, brand-token aware.

A pack turns a ``Source`` into a ``Draft`` (narration beats + scenes). Packs hold no
drawing code: the scenes are data for the shared motion runtime, so a new pack is a new
function here. Bump ``version`` when a pack's output changes: the version is part of a
video's idempotency key, so old videos stay as they were rendered.

Hook variants: ``A`` states the news, ``B`` asks the viewer's question (the staff A/B test).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from apps.distribution.video import art
from apps.distribution.video.plan import MAX_MS, MIN_MS, Beat, Brand, Draft, SceneSpec
from apps.distribution.video.sources import (
    KIND_ROUNDUP,
    KIND_SPOTLIGHT,
    Source,
)

Builder = Callable[[Source, str, Brand], Draft]
SAFE_SENTENCE = 150


@dataclass(frozen=True, slots=True)
class Pack:
    slug: str
    version: int
    label: str
    kinds: tuple[str, ...]
    build: Builder
    min_ms: int = MIN_MS
    max_ms: int = MAX_MS
    description: str = ""


def _seed(source: Source) -> int:
    return int(
        hashlib.sha256(f"{source.kind}:{source.ref}".encode()).hexdigest()[:8], 16
    )


def _clip(text: str, limit: int = SAFE_SENTENCE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cta(
    source: Source, brand: Brand, text: str, button: str
) -> tuple[Beat, SceneSpec]:
    beat = Beat("cta", _clip(f"{text} {brand.site}."))
    scene = SceneSpec(
        "cta",
        "cta",
        {
            "headline": _clip(text, 60),
            "button": button,
            "site": brand.site,
            "qr": art.qr_path(source.url) if source.url else None,
        },
        ("cta",),
        hold_ms=3600,
    )
    return beat, scene


# ------------------------------------------------------------------ firmware ----


def firmware_release(source: Source, hook: str, brand: Brand) -> Draft:
    device = source.device
    question = hook == "B"
    hook_text = (
        f"Looking for {device} firmware?"
        if question
        else f"New firmware for {device} just dropped."
    )
    beats = [Beat("hook", _clip(hook_text))]
    chips = [
        c
        for c in (
            source.chipset,
            f"Android {source.android_version}" if source.android_version else "",
        )
        if c
    ]
    scenes = [
        SceneSpec(
            "hook",
            "hook",
            {
                "kicker": "Firmware" if question else "New firmware",
                "headline": _clip(device, 60),
                "sub": "We have it, verified."
                if question
                else "Verified and ready to download.",
                "chips": chips[:2],
            },
            ("hook",),
            hold_ms=3200,
        )
    ]
    detail = " ".join(
        p
        for p in (
            f"{device}.",
            f"Build {source.build_number}." if source.build_number else "",
            f"Android {source.android_version}." if source.android_version else "",
        )
        if p
    )
    beats.append(Beat("device", _clip(detail)))
    scenes.append(
        SceneSpec(
            "device",
            "device",
            {
                "name": _clip(
                    source.variant and f"{device} {source.variant}" or device, 60
                ),
                "model": source.model,
                "initial": (source.brand or device)[:1].upper(),
                "image": art.local_image(source.image_url),
                "chips": [c for c in (source.chipset, source.build_number) if c][:3],
                "lower": brand.site,
                "verified": True,
            },
            ("device",),
            hold_ms=3600,
        )
    )
    stats: list[dict[str, str | int | float]] = []
    if source.android_version:
        stats.append({"label": "Android", "value": source.android_version})
    if source.build_number:
        stats.append({"label": "Build", "value": _clip(source.build_number, 18)})
    if source.size_mb:
        stats.append(
            {"label": "Size", "num": source.size_mb, "decimals": 1, "unit": "MB"}
        )
    if source.chipset:
        stats.append({"label": "Chipset", "value": _clip(source.chipset, 18)})
    if stats:
        facts_text = " ".join(
            p
            for p in (
                f"{source.chipset} chipset." if source.chipset else "",
                f"{source.size_mb} megabytes." if source.size_mb else "",
                "Checked by our team before it was listed.",
            )
            if p
        )
        beats.append(Beat("facts", _clip(facts_text)))
        scenes.append(
            SceneSpec(
                "facts",
                "facts",
                {"title": "The details", "stats": stats[:4], "badge": "VERIFIED"},
                ("facts",),
                hold_ms=3600,
            )
        )
    beats.append(
        Beat(
            "warn",
            "Always match the exact model and variant before you flash.",
            optional=True,
        )
    )
    scenes.append(
        SceneSpec(
            "warn",
            "warning",
            {
                "title": "Before you flash",
                "text": "Back up your data and use the firmware made for your exact model.",
            },
            ("warn",),
            hold_ms=3200,
        )
    )
    cta_beat, cta_scene = _cta(source, brand, "Download on", "Get the firmware")
    beats.append(cta_beat)
    scenes.append(cta_scene)
    return Draft(tuple(beats), tuple(scenes), _seed(source))


# ---------------------------------------------------------------------- blog ----


def _headline(title: str) -> str:
    return _clip(title, 80)


def blog_post(source: Source, hook: str, brand: Brand) -> Draft:
    question = hook == "B"
    hook_text = (
        f"Want to know: {_clip(source.title, 80)}?"
        if question
        else f"New on {brand.name}: {_clip(source.title, 90)}."
    )
    beats = [Beat("hook", _clip(hook_text))]
    chips = [f"{source.reading_minutes} min read"] if source.reading_minutes else []
    chips += list(source.tags[:2])
    scenes = [
        SceneSpec(
            "hook",
            "hook",
            {
                "kicker": "Read this" if question else "New article",
                "headline": _headline(source.title),
                "sub": _clip(source.summary, 110),
                "chips": chips[:3],
            },
            ("hook",),
            hold_ms=3600,
        )
    ]
    points = list(source.points)
    if points:
        for index, point in enumerate(points[:4], start=1):
            beats.append(Beat(f"p{index}", _clip(point, 100)))
        scenes.append(
            SceneSpec(
                "points",
                "points",
                {
                    "title": "Key points",
                    "items": [_clip(p, 70) for p in points[:4]],
                    "sync": True,
                },
                tuple(f"p{i}" for i in range(1, len(points[:4]) + 1)),
                hold_ms=3600,
            )
        )
    cta_beat, cta_scene = _cta(source, brand, "Read it on", "Read the article")
    beats.append(cta_beat)
    scenes.append(cta_scene)
    return Draft(tuple(beats), tuple(scenes), _seed(source))


_STEP_ICONS = ("usb", "phone", "power", "bolt", "check")


def flashing_guide(source: Source, hook: str, brand: Brand) -> Draft:
    steps = list(source.steps) or list(source.points)
    if not steps:
        return blog_post(source, hook, brand)
    steps = steps[:5]
    question = hook == "B"
    hook_text = (
        "Stuck on flashing? Here is how."
        if question
        else f"Step by step: {_clip(source.title, 90)}."
    )
    beats = [Beat("hook", _clip(hook_text))]
    meta_chips = [c for c in source.tags[:2]]
    scenes = [
        SceneSpec(
            "hook",
            "hook",
            {
                "kicker": "Flashing guide",
                "headline": _headline(source.title),
                "sub": "Follow along, one step at a time.",
                "chips": meta_chips,
            },
            ("hook",),
            hold_ms=3400,
        )
    ]
    for index, step in enumerate(steps, start=1):
        beats.append(Beat(f"s{index}", _clip(f"Step {index}. {step}", 130)))
    scenes.append(
        SceneSpec(
            "steps",
            "steps",
            {
                "title": "How it works",
                "tool": "",
                "chipset": source.chipset,
                "steps": [
                    {"text": _clip(s, 80), "icon": _STEP_ICONS[i % len(_STEP_ICONS)]}
                    for i, s in enumerate(steps)
                ],
                "sync": True,
            },
            tuple(f"s{i}" for i in range(1, len(steps) + 1)),
            hold_ms=4000,
        )
    )
    beats.append(
        Beat(
            "warn",
            "Back up your data first. Flashing can erase the phone.",
            optional=True,
        )
    )
    scenes.append(
        SceneSpec(
            "warn",
            "warning",
            {
                "title": "Careful",
                "text": "Back up first. Use files made for your exact model.",
            },
            ("warn",),
            hold_ms=3000,
        )
    )
    cta_beat, cta_scene = _cta(source, brand, "Full guide on", "Open the guide")
    beats.append(cta_beat)
    scenes.append(cta_scene)
    return Draft(tuple(beats), tuple(scenes), _seed(source))


# ------------------------------------------------------- spotlight, roundup ----


def spotlight(source: Source, hook: str, brand: Brand) -> Draft:
    question = hook == "B"
    name = source.brand or source.chipset or source.title
    hook_text = (
        f"Which {name} firmware do you need?"
        if question
        else f"{name} firmware, all in one place."
    )
    beats = [Beat("hook", _clip(hook_text))]
    scenes = [
        SceneSpec(
            "hook",
            "hook",
            {
                "kicker": "Spotlight",
                "headline": _clip(name, 50),
                "sub": source.summary,
                "chips": [],
            },
            ("hook",),
            hold_ms=3400,
        )
    ]
    if source.items:
        for index, (title, _sub) in enumerate(source.items, start=1):
            beats.append(Beat(f"i{index}", _clip(title, 90)))
        scenes.append(
            SceneSpec(
                "list",
                "list",
                {
                    "title": "Latest verified",
                    "items": [{"title": t, "sub": s} for t, s in source.items],
                    "sync": True,
                },
                tuple(f"i{i}" for i in range(1, len(source.items) + 1)),
                hold_ms=3800,
            )
        )
    cta_beat, cta_scene = _cta(source, brand, "Browse them on", "See all firmware")
    beats.append(cta_beat)
    scenes.append(cta_scene)
    return Draft(tuple(beats), tuple(scenes), _seed(source))


def weekly_roundup(source: Source, hook: str, brand: Brand) -> Draft:
    question = hook == "B"
    hook_text = (
        "What new firmware landed this week?"
        if question
        else "This week's firmware roundup."
    )
    beats = [Beat("hook", hook_text)]
    scenes = [
        SceneSpec(
            "hook",
            "hook",
            {
                "kicker": "Weekly roundup",
                "headline": "This week's firmware",
                "sub": source.summary,
                "chips": [],
            },
            ("hook",),
            hold_ms=3400,
        )
    ]
    if source.bars:
        beats.append(Beat("chart", _clip(f"{source.summary}, by brand.")))
        scenes.append(
            SceneSpec(
                "chart",
                "chart",
                {
                    "title": "New by brand",
                    "bars": [
                        {"label": _clip(b, 14), "value": v} for b, v in source.bars
                    ],
                },
                ("chart",),
                hold_ms=4000,
            )
        )
    if source.items:
        for index, (title, _sub) in enumerate(source.items, start=1):
            beats.append(Beat(f"i{index}", _clip(title, 90)))
        scenes.append(
            SceneSpec(
                "list",
                "list",
                {
                    "title": "Newest",
                    "items": [{"title": t, "sub": s} for t, s in source.items],
                    "sync": True,
                },
                tuple(f"i{i}" for i in range(1, len(source.items) + 1)),
                hold_ms=3800,
            )
        )
    cta_beat, cta_scene = _cta(source, brand, "All of it on", "See what is new")
    beats.append(cta_beat)
    scenes.append(cta_scene)
    return Draft(tuple(beats), tuple(scenes), _seed(source))


def smoke(source: Source, hook: str, brand: Brand) -> Draft:
    """A 3-second self-test clip (capability check and golden test); never auto-enlisted."""
    del source, hook
    return Draft(
        (Beat("a", "Render check."), Beat("b", "All systems go.")),
        (
            SceneSpec(
                "hook",
                "hook",
                {
                    "kicker": "Self-test",
                    "headline": "Render OK",
                    "sub": brand.site,
                    "chips": ["H.264", "30 fps"],
                },
                ("a",),
                hold_ms=1500,
            ),
            SceneSpec(
                "facts",
                "facts",
                {
                    "title": "Motion engine",
                    "stats": [
                        {"label": "Frames", "num": 90},
                        {"label": "Fps", "num": 30},
                    ],
                    "badge": "OK",
                },
                ("b",),
                hold_ms=1500,
            ),
        ),
        7,
    )


PACKS: dict[str, Pack] = {
    "firmware-release": Pack(
        "firmware-release",
        1,
        "Firmware release",
        ("firmware",),
        firmware_release,
        description="Device hero, facts counters, verified stamp, warning card, QR call to action.",
    ),
    "flashing-guide": Pack(
        "flashing-guide",
        1,
        "Flashing guide",
        ("blog",),
        flashing_guide,
        description="Numbered steps with animated tool icons and a warning card.",
    ),
    "blog-post": Pack(
        "blog-post",
        1,
        "Blog post",
        ("blog",),
        blog_post,
        description="Kinetic headline, key points, reading time.",
    ),
    "spotlight": Pack(
        "spotlight",
        1,
        "Brand or chipset spotlight",
        (KIND_SPOTLIGHT,),
        spotlight,
        description="Latest verified firmware for one brand or chipset.",
    ),
    "weekly-roundup": Pack(
        "weekly-roundup",
        1,
        "Weekly roundup",
        (KIND_ROUNDUP,),
        weekly_roundup,
        description="This week's firmware with an animated chart and list.",
    ),
    "smoke": Pack(
        "smoke",
        1,
        "Self-test",
        ("smoke",),
        smoke,
        min_ms=2500,
        max_ms=6000,
        description="Three-second capability check.",
    ),
}
USER_PACKS = tuple(p for p in PACKS.values() if p.slug != "smoke")


def default_pack(source: Source) -> Pack:
    """The pack a fresh draft uses for this content."""
    if source.kind == "firmware":
        return PACKS["firmware-release"]
    if source.kind == "blog":
        return PACKS[
            "flashing-guide" if source.is_guide and source.steps else "blog-post"
        ]
    if source.kind == KIND_ROUNDUP:
        return PACKS["weekly-roundup"]
    return PACKS["spotlight"]


def packs_for(kind: str) -> list[Pack]:
    return [p for p in USER_PACKS if kind in p.kinds]
