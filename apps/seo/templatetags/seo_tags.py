"""Template integration: ``{% seo_head %}`` renders the one resolved page head."""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.seo import schema
from apps.seo.resolver import resolve
from apps.site_settings.services import get_site_settings

register = template.Library()


@register.inclusion_tag("seo/related_links.html")
def related_links(obj: object, limit: int = 8) -> dict[str, object]:
    """``{% related_links object %}``: reviewed internal links out of ``object``."""
    from apps.seo import api

    return {"links": api.related_links(obj, limit)}


@register.simple_tag(takes_context=True)
def seo_head(context: template.Context) -> SafeString:
    """Title, description, robots, canonical, hreflang, Open Graph, Twitter, JSON-LD.

    Rendered once from ``base.html``; child templates must not emit these tags.
    """
    request = context.get("request")
    if request is None:
        return format_html("<title>{}</title>", get_site_settings().site_name)
    resolved = resolve(
        request, {str(key): value for key, value in context.flatten().items()}
    )
    rendered = render_to_string(
        "seo/head.html",
        {
            "seo": resolved,
            "og_items": list(resolved.og.items()),
            "twitter_items": list(resolved.twitter.items()),
            "json_ld": [schema.to_json_ld(node) for node in resolved.schema],
        },
    )
    return SafeString(rendered)
