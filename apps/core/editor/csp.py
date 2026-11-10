"""Allow privacy-friendly video players in ``frame-src`` only where they are used.

The base policy keeps ``frame-src 'none'``. A response gets the audited player hosts
(see :data:`apps.core.editor.video.VIDEO_FRAME_HOSTS`) only when its HTML actually
contains a sanitised embed or an editor that may produce one, so every other page
keeps the strictest policy. List it before the admin and analytics CSP middlewares:
responses unwind in reverse order, so this runs after them and merges into whatever
policy the view or those middlewares already attached.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from .video import VIDEO_FRAME_HOSTS

CSP_ATTRIBUTE = "_csp_config"
MARKERS = (
    b'class="mf-video"',
    b"videoProviders&quot;:[&quot;",
)


def _policy_for(response: HttpResponse) -> dict[str, list[str]]:
    source = getattr(response, CSP_ATTRIBUTE, settings.SECURE_CSP)
    return {
        name: [values] if isinstance(values, str) else list(values)
        for name, values in source.items()
    }


def _needs_players(response: HttpResponse) -> bool:
    if response.streaming or not response.headers.get("Content-Type", "").startswith(
        "text/html"
    ):
        return False
    content = response.content
    return any(marker in content for marker in MARKERS)


def video_embed_csp_middleware(
    get_response: Callable[[HttpRequest], HttpResponse],
) -> Callable[[HttpRequest], HttpResponse]:
    """Extend ``frame-src`` with the video player hosts for pages that need them."""

    def middleware(request: HttpRequest) -> HttpResponse:
        response = get_response(request)
        if not _needs_players(response):
            return response
        policy = _policy_for(response)
        frames = [value for value in policy.get("frame-src", []) if value != "'none'"]
        frames.extend(host for host in VIDEO_FRAME_HOSTS if host not in frames)
        policy["frame-src"] = frames
        setattr(response, CSP_ATTRIBUTE, policy)
        return response

    return middleware
