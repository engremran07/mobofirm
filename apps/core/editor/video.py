"""Privacy-friendly video embeds for the rich-text editor.

Only providers listed here can ever be embedded: each has an explicit URL pattern that
is rewritten to the provider's no-cookie / do-not-track player. Anything else is
dropped by the sanitiser, so an administrator can only choose among audited providers
and ``frame-src`` stays limited to ``VIDEO_FRAME_HOSTS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_VIMEO_ID = re.compile(r"^[0-9]{4,12}$")


@dataclass(frozen=True, slots=True)
class VideoProvider:
    """One audited video host and how its URLs are normalised."""

    key: str
    label: str
    hosts: frozenset[str]
    embed_host: str


PROVIDERS: dict[str, VideoProvider] = {
    "youtube": VideoProvider(
        "youtube",
        "YouTube (privacy-enhanced mode)",
        frozenset(
            {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "youtu.be",
                "youtube-nocookie.com",
                "www.youtube-nocookie.com",
            }
        ),
        "www.youtube-nocookie.com",
    ),
    "vimeo": VideoProvider(
        "vimeo",
        "Vimeo (do-not-track)",
        frozenset({"vimeo.com", "www.vimeo.com", "player.vimeo.com"}),
        "player.vimeo.com",
    ),
}

# Hosts the page CSP must allow in ``frame-src`` for the embeds above to render.
VIDEO_FRAME_HOSTS: tuple[str, ...] = tuple(
    f"https://{provider.embed_host}" for provider in PROVIDERS.values()
)

DEFAULT_PROVIDERS: tuple[str, ...] = tuple(PROVIDERS)


def provider_choices() -> list[tuple[str, str]]:
    """``(key, label)`` pairs for admin forms."""
    return [(key, provider.label) for key, provider in PROVIDERS.items()]


def normalize_providers(keys: object) -> tuple[str, ...]:
    """Known provider keys, in catalogue order; malformed input yields none."""
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return ()
    wanted = {key for key in keys if isinstance(key, str)}
    return tuple(key for key in PROVIDERS if key in wanted)


def _youtube_id(host: str, parts: list[str], query: str) -> str:
    if host == "youtu.be":
        return parts[0] if parts else ""
    if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
        return parts[1]
    if parts == ["watch"]:
        for chunk in query.split("&"):
            name, _sep, value = chunk.partition("=")
            if name == "v":
                return value
    return ""


def _vimeo_id(parts: list[str]) -> str:
    if len(parts) >= 2 and parts[0] == "video":
        return parts[1]
    return next((part for part in parts if part.isdigit()), "")


def rewrite_video_url(url: str, providers: tuple[str, ...]) -> str | None:
    """Return the privacy-friendly embed URL for ``url`` or ``None`` if not allowed."""
    value = url.strip()
    if value.startswith("//"):
        value = f"https:{value}"
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.username or port:
        return None
    host = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    for key in providers:
        provider = PROVIDERS.get(key)
        if provider is None or host not in provider.hosts:
            continue
        if key == "youtube":
            video_id = _youtube_id(host, parts, parsed.query)
            if _YOUTUBE_ID.fullmatch(video_id):
                return f"https://{provider.embed_host}/embed/{video_id}"
        elif key == "vimeo":
            video_id = _vimeo_id(parts)
            if _VIMEO_ID.fullmatch(video_id):
                return f"https://{provider.embed_host}/video/{video_id}?dnt=1"
    return None
