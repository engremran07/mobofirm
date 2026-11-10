"""Template filter and tag for rendering and editing stored rich text."""

from __future__ import annotations

from typing import Any

from django import template
from django.utils.safestring import SafeString

from apps.core.editor.sanitizer import plain_text as _plain_text
from apps.core.editor.sanitizer import render_rich_text
from apps.core.editor.widgets import RichTextWidget

register = template.Library()


@register.filter(name="plain_text")
def plain_text(value: str | None) -> str:
    """The visible text of rich content on one line (escaped again on output).

    For cards, truncated snippets and meta tags, where markup must not leak:
    ``{{ tool.description|plain_text|truncatewords:20 }}``.
    """
    return _plain_text(value)


@register.filter(name="rich_text")
def rich_text(value: str | None) -> SafeString:
    """Re-sanitise ``value`` at render time.

    Content stays valid after an editor feature is switched off, so rendering uses
    the widest policy rather than the current profile.

    Usage: ``{{ post.body|rich_text }}``.
    """
    return render_rich_text(value)


@register.simple_tag
def rich_text_field(
    profile: str, name: str, value: str = "", **attrs: Any
) -> SafeString:
    """Render a textarea upgraded by the editor of ``profile`` (no Django form).

    Usage: ``{% rich_text_field "comment" "body" id="c-body" rows=4 %}``.
    """
    widget = RichTextWidget(
        profile, attrs={key.replace("_", "-"): val for key, val in attrs.items()}
    )
    return widget.render(name, value)
