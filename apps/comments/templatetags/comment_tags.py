"""Template tags for comment-specific presentation."""

from __future__ import annotations

from django import template
from django.http import HttpRequest
from django.utils.safestring import SafeString

from apps.comments.rendering import render_comment_body

register = template.Library()


@register.simple_tag(takes_context=True)
def comment_rich_text(context: template.Context, value: str | None) -> SafeString:
    request = context.get("request")
    if not isinstance(request, HttpRequest):
        raise TypeError("comment_rich_text requires the request context")
    return render_comment_body(
        value, host=request.get_host(), secure=request.is_secure()
    )
