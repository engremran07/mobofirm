"""Template tag for the "Request a new method" button shown on article pages."""

from __future__ import annotations

from typing import Any

from django import template

from apps.blog.access import blog_settings
from apps.blog.models import Post

register = template.Library()


@register.inclusion_tag("blog/partials/request_method.html")
def request_method_cta(post: Post) -> dict[str, Any]:
    """Button that opens the guide-request form pre-filled with ``post``."""
    return {"post": post, "enabled": blog_settings().allow_guide_requests}
