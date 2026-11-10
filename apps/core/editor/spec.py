"""Editor profiles: the per-context configuration of the rich-text editor.

``DEFAULT_PROFILES`` is the code-owned baseline that works with an empty database
(fresh installs, tests, migrations). Administrators override it through the
``site_settings.EditorProfile`` rows; that app registers a *profile source* here at
start-up so ``apps.core`` never imports it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .features import ALL_FEATURE_KEYS, normalize_features
from .video import DEFAULT_PROVIDERS, normalize_providers

CONTEXT_BLOG_POST = "blog_post"
CONTEXT_GUIDE_REQUEST = "guide_request"
CONTEXT_COMMENT = "comment"
CONTEXT_PAGE = "page"
CONTEXT_LISTING = "listing"
CONTEXT_FIRMWARE_NOTES = "firmware_notes"
CONTEXT_ANNOUNCEMENT = "announcement"
CONTEXT_REQUEST = "request"
CONTEXT_ADMIN_DEFAULT = "admin_default"

CONTEXT_LABELS: dict[str, str] = {
    CONTEXT_BLOG_POST: "Blog posts",
    CONTEXT_GUIDE_REQUEST: "Guide requests and solutions",
    CONTEXT_COMMENT: "Comments",
    CONTEXT_PAGE: "CMS pages",
    CONTEXT_LISTING: "Marketplace listings",
    CONTEXT_FIRMWARE_NOTES: "Firmware release notes",
    CONTEXT_ANNOUNCEMENT: "Announcements and promotions",
    CONTEXT_REQUEST: "Catalogue and firmware requests",
    CONTEXT_ADMIN_DEFAULT: "Other admin text fields",
}

MIN_HEIGHT = 120
MAX_HEIGHT = 900
MAX_UPLOAD_KB = 10240

ProfileSource = Callable[[str], "ProfileSpec | None"]
_sources: list[ProfileSource] = []


@dataclass(frozen=True, slots=True)
class UploadRules:
    """Whether and how editors may upload images for this context."""

    enabled: bool = False
    max_kb: int = 2048
    daily_limit: int = 20


@dataclass(frozen=True, slots=True)
class Layout:
    """Presentation and length limits of the editing area."""

    max_length: int = 0
    spellcheck: bool = True
    placeholder: str = ""
    height: int = 280


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    """Immutable, fully resolved editor configuration for one context."""

    key: str
    enabled: bool = True
    features: frozenset[str] = field(default_factory=frozenset)
    video_providers: tuple[str, ...] = DEFAULT_PROVIDERS
    upload: UploadRules = field(default_factory=UploadRules)
    layout: Layout = field(default_factory=Layout)

    @property
    def label(self) -> str:
        """Human readable name of the context."""
        return CONTEXT_LABELS.get(self.key, self.key)

    def allows(self, feature: str) -> bool:
        """Whether ``feature`` is switched on in this profile."""
        return feature in self.features

    def field_values(self) -> dict[str, Any]:
        """The values of the ``site_settings.EditorProfile`` columns for this spec."""
        return {
            "enabled": self.enabled,
            "features": sorted(self.features),
            "video_providers": list(self.video_providers),
            "upload_enabled": self.upload.enabled,
            "upload_max_kb": self.upload.max_kb,
            "upload_daily_limit": self.upload.daily_limit,
            "max_length": self.layout.max_length,
            "spellcheck": self.layout.spellcheck,
            "placeholder": self.layout.placeholder,
            "height": self.layout.height,
        }

    @property
    def images_enabled(self) -> bool:
        """Images may appear in the content."""
        return "images" in self.features

    @property
    def upload_available(self) -> bool:
        """Editors may upload images (needs the images feature too)."""
        return self.upload.enabled and self.images_enabled

    @property
    def video_enabled(self) -> bool:
        """Video embeds are on and at least one provider is allowed."""
        return "video" in self.features and bool(self.video_providers)


_FULL = ALL_FEATURE_KEYS
_TEXT = frozenset({"bold", "italic", "underline", "strike", "undo_redo"})
_REDUCED = frozenset({"bold", "italic", "strike", "lists", "links", "emoji"})
_ARTICLE = _TEXT | {
    "script",
    "style",
    "lists",
    "links",
    "tables",
    "images",
    "emoji",
    "fullscreen",
    "clear_format",
    "help",
    "hr",
}


def _uploads(max_kb: int, daily_limit: int) -> UploadRules:
    return UploadRules(enabled=True, max_kb=max_kb, daily_limit=daily_limit)


DEFAULT_PROFILES: dict[str, ProfileSpec] = {
    CONTEXT_BLOG_POST: ProfileSpec(
        CONTEXT_BLOG_POST,
        features=_FULL,
        upload=_uploads(4096, 50),
        layout=Layout(100_000, True, "Write the article…", 420),
    ),
    CONTEXT_GUIDE_REQUEST: ProfileSpec(
        CONTEXT_GUIDE_REQUEST,
        features=_ARTICLE,
        upload=_uploads(2048, 10),
        layout=Layout(
            20_000, True, "Describe the device, what you tried and what happened…", 300
        ),
    ),
    CONTEXT_COMMENT: ProfileSpec(
        CONTEXT_COMMENT,
        features=_REDUCED,
        layout=Layout(5_000, True, "Add a comment…", 160),
    ),
    CONTEXT_PAGE: ProfileSpec(
        CONTEXT_PAGE,
        features=_FULL,
        upload=_uploads(4096, 50),
        layout=Layout(200_000, True, "", 480),
    ),
    CONTEXT_LISTING: ProfileSpec(
        CONTEXT_LISTING,
        features=_ARTICLE - {"tables", "hr", "script"},
        upload=_uploads(2048, 10),
        layout=Layout(20_000, True, "", 280),
    ),
    CONTEXT_FIRMWARE_NOTES: ProfileSpec(
        CONTEXT_FIRMWARE_NOTES,
        features=_ARTICLE - {"images", "emoji", "hr"},
        layout=Layout(20_000, True, "", 240),
    ),
    CONTEXT_ANNOUNCEMENT: ProfileSpec(
        CONTEXT_ANNOUNCEMENT,
        features=_FULL - {"video", "tables"},
        upload=_uploads(2048, 20),
        layout=Layout(10_000, True, "", 240),
    ),
    CONTEXT_REQUEST: ProfileSpec(
        CONTEXT_REQUEST,
        features=_REDUCED | {"undo_redo", "style"},
        layout=Layout(5_000, True, "", 180),
    ),
    CONTEXT_ADMIN_DEFAULT: ProfileSpec(
        CONTEXT_ADMIN_DEFAULT,
        features=_FULL,
        upload=_uploads(4096, 100),
        layout=Layout(0, True, "", 320),
    ),
}


def register_profile_source(source: ProfileSource) -> None:
    """Add a database-backed override source (idempotent per callable)."""
    if source not in _sources:
        _sources.append(source)


def default_profile(key: str) -> ProfileSpec:
    """The code-owned baseline for ``key`` (unknown keys fall back to admin)."""
    base = DEFAULT_PROFILES.get(key) or DEFAULT_PROFILES[CONTEXT_ADMIN_DEFAULT]
    return base if base.key == key else replace(base, key=key)


def clamp_spec(spec: ProfileSpec) -> ProfileSpec:
    """Return ``spec`` with every field forced into its supported range."""
    layout = spec.layout
    upload = spec.upload
    return replace(
        spec,
        features=normalize_features(spec.features),
        video_providers=normalize_providers(spec.video_providers),
        upload=replace(
            upload,
            max_kb=min(max(upload.max_kb, 1), MAX_UPLOAD_KB),
            daily_limit=max(upload.daily_limit, 0),
        ),
        layout=replace(
            layout,
            height=min(max(layout.height, MIN_HEIGHT), MAX_HEIGHT),
            max_length=max(layout.max_length, 0),
            placeholder=layout.placeholder[:200],
        ),
    )


def get_profile(key: str) -> ProfileSpec:
    """Resolve the effective profile: admin override first, code default otherwise."""
    for source in _sources:
        spec = source(key)
        if spec is not None:
            return clamp_spec(spec)
    return clamp_spec(default_profile(key))
