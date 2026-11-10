"""Rich-text editor foundation shared by every app that edits formatted text.

Public surface: resolve a profile (:func:`get_profile`), sanitise and render with it
(:func:`sanitize_rich_text`, :func:`render_rich_text`), and attach the editor to a form
field (``apps.core.editor.widgets.RichTextWidget``). Profile overrides live in the
``site_settings`` app and are plugged in through :func:`register_profile_source`.
"""

from __future__ import annotations

from .sanitizer import (
    render_rich_text,
    sanitize_rich_text,
    sanitize_stored_html,
    text_content,
    text_length,
    text_with_links,
)
from .spec import (
    CONTEXT_LABELS,
    DEFAULT_PROFILES,
    ProfileSpec,
    default_profile,
    get_profile,
    register_profile_source,
)

__all__ = [
    "CONTEXT_LABELS",
    "DEFAULT_PROFILES",
    "ProfileSpec",
    "default_profile",
    "get_profile",
    "register_profile_source",
    "render_rich_text",
    "sanitize_rich_text",
    "sanitize_stored_html",
    "text_content",
    "text_length",
    "text_with_links",
]
