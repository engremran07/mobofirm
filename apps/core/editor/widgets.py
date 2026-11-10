"""The textarea widget that turns into the Summernote editor in the browser.

The widget is plain HTML: a ``<textarea>`` carrying a JSON configuration in a data
attribute, plus a module ``<script>`` that lazily loads the editor bundle. With the
profile switched off (or JavaScript unavailable) the textarea simply stays a textarea,
so forms never depend on the editor to work.
"""

from __future__ import annotations

import json
from typing import Any

from django import forms
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape, format_html, linebreaks
from django.utils.safestring import SafeString

from .sanitizer import HTML_HINT
from .spec import ProfileSpec, get_profile

UPLOAD_URL_NAME = "site_settings:editor_upload"
LOADER_ASSET = "build/editor.js"


def upload_url() -> str:
    """Endpoint that stores editor images, or ``""`` when it is not routed."""
    try:
        return reverse(UPLOAD_URL_NAME)
    except NoReverseMatch:
        return ""


def client_config(spec: ProfileSpec, visible_limit: int = 0) -> dict[str, Any]:
    """The JSON-safe configuration the browser editor is built from.

    ``visible_limit`` is a field's own cap on visible text; the stricter of it and the
    profile's limit is shown by the character counter and enforced on submit.
    """
    endpoint = upload_url() if spec.upload_available else ""
    return {
        "profile": spec.key,
        "features": sorted(spec.features),
        "videoProviders": list(spec.video_providers) if spec.video_enabled else [],
        "upload": {
            "enabled": bool(endpoint),
            "url": endpoint,
            "maxKb": spec.upload.max_kb,
        },
        "maxLength": min(
            (limit for limit in (spec.layout.max_length, visible_limit) if limit),
            default=0,
        ),
        "spellcheck": spec.layout.spellcheck,
        "placeholder": spec.layout.placeholder,
        "height": spec.layout.height,
    }


class RichTextWidget(forms.Textarea):
    """A textarea progressively enhanced by the profile's rich-text editor."""

    def __init__(
        self,
        profile: str = "admin_default",
        attrs: dict[str, Any] | None = None,
        visible_limit: int = 0,
    ) -> None:
        self.profile = profile
        self.visible_limit = visible_limit
        super().__init__(attrs={"rows": 8, **(attrs or {})})

    def build_attrs(
        self, base_attrs: dict[str, Any], extra_attrs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Add the editor hooks when the profile is enabled."""
        attrs = super().build_attrs(base_attrs, extra_attrs)
        spec = get_profile(self.profile)
        attrs["spellcheck"] = "true" if spec.layout.spellcheck else "false"
        if spec.enabled:
            attrs["data-mf-editor"] = "1"
            attrs["data-mf-editor-config"] = json.dumps(
                client_config(spec, self.visible_limit), separators=(",", ":")
            )
        return attrs

    def format_value(self, value: Any) -> str | None:
        """Show legacy plain text (no markup) as paragraphs inside the editor."""
        formatted = super().format_value(value)
        if (
            formatted
            and get_profile(self.profile).enabled
            and not HTML_HINT.search(formatted)
        ):
            return str(linebreaks(escape(formatted)))
        return formatted

    def render(
        self,
        name: str,
        value: Any,
        attrs: dict[str, Any] | None = None,
        renderer: Any = None,
    ) -> SafeString:
        """Render the textarea followed by the (deduplicated) loader script."""
        markup = super().render(name, value, attrs, renderer)
        if not get_profile(self.profile).enabled:
            return markup
        loader = format_html(
            '<script type="module" src="{}"></script>', static(LOADER_ASSET)
        )
        return SafeString(f"{markup}{loader}")
