"""Head links so browsers, feed readers and Android apps can find the feeds."""

from __future__ import annotations

from django import template
from django.utils.html import format_html_join
from django.utils.safestring import SafeString

from apps.distribution import feed_views, feeds

register = template.Library()


@register.simple_tag
def feed_autodiscovery() -> SafeString:
    """``<link rel="alternate">`` for the site-wide feeds; nothing while feeds are off."""
    if not feed_views.feeds_enabled():
        return SafeString("")
    rows = [
        (mime, label, feeds.feed_url(feeds.SITE_KEY, fmt))
        for fmt, (mime, label) in feeds.FORMATS.items()
    ]
    return format_html_join(
        "\n  ",
        '<link rel="alternate" type="{}" title="Updates ({})" href="{}">',
        rows,
    )
