"""Template tags for rendering ad zones and code-addressed slots in public templates."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from django import template
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from apps.ads.models import AdPlacement, AdsSettings
from apps.ads.services import auto_slots
from apps.ads.services import zones as zone_service
from apps.ads.services.markup import (
    render_creative,
)
from apps.ads.services.markup import (
    safe_click_url as _safe_click_url,
)
from apps.ads.services.markup import (
    validate_ad_creative_code as _validate_ad_creative_code,
)
from apps.ads.services.rotation.engine import choose_creative
from apps.consent.constants import ADVERTISING
from apps.consent.services import has_consent

register = template.Library()
logger = logging.getLogger(__name__)

__all__ = [
    "_safe_click_url",
    "_validate_ad_creative_code",
    "ad_article_body",
    "ad_tile",
    "ad_zone",
    "render_ad_slot",
]

_MODE_LABELS = {
    "test": "TEST AD",
    "paused": "PAUSED ZONE",
    "unplaced": "EMPTY ZONE",
}


def _template_name(context: template.Context) -> str:
    """The page template being rendered (an included partial reports its page)."""
    current = getattr(context, "template", None)
    return str(getattr(current, "name", "") or "")


def _zone_markup(render: zone_service.ZoneRender, css_class: str) -> str:
    zone = render.zone
    classes = f"ad-zone ad-zone--{zone.code} ad-zone--s{render.size}"
    if render.mode != "live":
        classes += " ad-zone--test"
    if css_class:
        classes += f" {escape(css_class)}"
    placement = render.placement
    if render.mode == "live":
        body = render.html
        data = f'data-placement="{escape(placement.slug if placement else "")}"'
    else:
        detail = (
            f"placement #{placement.pk}" if placement else "not placed on this template"
        )
        if render.mode == "paused":
            detail += " (switched off)"
        body = (
            f'<span class="ad-zone__test" data-ad-test="{render.mode}">'
            f"{_MODE_LABELS[render.mode]} &mdash; {escape(render.size)} "
            f"&mdash; zone {escape(zone.code)} &mdash; {escape(detail)}</span>"
        )
        data = f'data-ad-test="{render.mode}"'
    inner = (
        f'<aside class="{classes}" aria-label="Advertisement" '
        f'data-ad-zone="{escape(zone.code)}" {data}>'
        f'<span class="ad-zone__label">Advertisement</span>'
        f'<div class="ad-zone__body">{body}</div></aside>'
    )
    if zone.contained:
        return f'<div class="site-container ad-zone-wrap">{inner}</div>'
    return inner


@register.simple_tag(takes_context=True)
def ad_zone(
    context: template.Context, zone_code: str, css_class: str = ""
) -> SafeString:
    """Declare an ad zone. Renders nothing unless the zone is placed and allowed.

    Usage::

        {% load ad_tags %}
        {% ad_zone "after_content" %}
        {% ad_zone "sidebar" css_class="my-4" %}

    ``zone_code`` is one of ``apps.ads.zones.ZONES``. Which template shows which zone,
    and which campaign fills it, is data managed in the placement manager.
    """
    request = context.get("request")
    if not isinstance(request, HttpRequest):
        return mark_safe("")
    render = zone_service.resolve_zone(request, _template_name(context), zone_code)
    if render is None:
        return mark_safe("")
    return mark_safe(_zone_markup(render, css_class))


@register.simple_tag(takes_context=True)
def ad_article_body(context: template.Context, html: Any) -> SafeString:
    """The article body with automatic in-article ad slots between paragraphs.

    Usage: ``{% ad_article_body article.html %}`` instead of ``{{ article.html }}``.
    The (already sanitised, possibly cached) article HTML is left untouched: slots are
    spliced in per request, after every K-th/N-th text paragraph, never inside lists,
    tables, figures, quotes or code and never next to a heading. Without a live
    ``in_article`` placement, consent or an ad-eligible visitor the body is returned as is.
    """
    body = str(html)
    request = context.get("request")
    if not isinstance(request, HttpRequest) or not body:
        return mark_safe(body)
    page = _template_name(context)
    serving, placement = zone_service.auto_placement(
        request, page, auto_slots.IN_ARTICLE
    )
    if not serving:
        return mark_safe(body)
    rule = auto_slots.rule_for(placement, auto_slots.IN_ARTICLE)

    def slot() -> str:
        render = zone_service.resolve_zone(
            request, page, auto_slots.IN_ARTICLE, unique=True
        )
        return _zone_markup(render, "ad-zone--inline") if render else ""

    cap = min(
        zone_service.auto_budget(request),
        rule.max_slots - zone_service.auto_used(request, auto_slots.IN_ARTICLE),
    )
    return mark_safe(
        auto_slots.inject_slots(
            body,
            rule,
            slot,
            min_words_between=zone_service.min_words_between_ads(),
            cap=cap,
        )
    )


def _tile_context(
    render: zone_service.ZoneRender, layout: str, tile: str
) -> dict[str, Any]:
    """Public text and image of a sponsored tile (never internal creative names)."""
    zone = render.zone
    context: dict[str, Any] = {
        "layout": layout,
        "tile": tile,
        "zone": zone.code,
        "placement": render.placement.slug if render.placement else "",
        "title": "",
        "text": "",
        "href": "",
        "image": "",
        "raw": "",
        "test": "",
    }
    if render.mode != "live":
        context.update(
            title="Test ad",
            text=f"{_MODE_LABELS[render.mode].title()} · zone {zone.code}",
            test=render.mode,
        )
        return context
    creative = render.creative
    placement = render.placement
    if creative is None or placement is None:
        return context
    href = zone_service.click_url(placement, creative)
    if creative.creative_type == "banner":
        host = urlsplit(creative.click_url).hostname or ""
        context.update(
            title=creative.headline or (f"Visit {host}" if host else "Sponsored"),
            text=creative.tagline,
            href=href,
            image=creative.image_url
            if creative.image_url.startswith("https://")
            else "",
        )
    else:
        context["raw"] = mark_safe(render.html)
    return context


@register.simple_tag(takes_context=True)
def ad_tile(
    context: template.Context,
    position: Any,
    total: Any,
    layout: str = "tile",
    tile: str = "",
    page: str = "",
) -> SafeString:
    """A sponsored grid item after the ``position``-th of ``total`` tiles, if due.

    Usage inside the tile loop, right after the tile::

        {% for brand in brands %}{% include "…/brand_tile.html" %}{% ad_tile forloop.counter brands|length tile="card-ui--tile card-ui--brand-tile" page="firmwares/brand_list.html" %}{% endfor %}

    ``tile`` repeats the sibling's modifier classes (so the ad has exactly the same size,
    radius and elevation); ``layout`` is ``tile`` (logo row), ``card`` (body + footer) or
    ``tag`` (a ``<li>``); ``page`` is the page template the placement is keyed on, so a
    full page and its HTMX fragment resolve alike. Which positions get an ad is detected
    from the count of tiles (``AdPlacement.auto_*``): never fewer than the minimum, never
    the last tile of the page, never above the placement's maximum.
    """
    request = context.get("request")
    if not isinstance(request, HttpRequest):
        return mark_safe("")
    try:
        index, count = int(position), int(total)
    except (TypeError, ValueError):
        return mark_safe("")
    page_name = page or _template_name(context)
    serving, placement = zone_service.auto_placement(
        request, page_name, auto_slots.BETWEEN_ITEMS
    )
    if not serving:
        return mark_safe("")
    rule = auto_slots.rule_for(placement, auto_slots.BETWEEN_ITEMS)
    # Positions come from this grid alone; the page budget and the placement maximum
    # (across every grid of the page) only decide whether this one is still allowed.
    if index not in auto_slots.tile_slots(count, rule):
        return mark_safe("")
    if (
        zone_service.auto_budget(request) <= 0
        or zone_service.auto_used(request, auto_slots.BETWEEN_ITEMS) >= rule.max_slots
    ):
        return mark_safe("")
    render = zone_service.resolve_zone(
        request, page_name, auto_slots.BETWEEN_ITEMS, unique=True
    )
    if render is None:
        return mark_safe("")
    layout = layout if layout in {"tile", "card", "tag", "guide"} else "tile"
    return mark_safe(
        render_to_string(
            "ads/includes/tile_ad.html", _tile_context(render, layout, tile)
        )
    )


@register.simple_tag(takes_context=True)
def render_ad_slot(
    context: template.Context,
    slot_code: str,
    css_class: str = "",
) -> str:
    """Render a placement addressed by its code (used by the rewarded-ad page).

    Prefer ``{% ad_zone %}`` for layout slots. Without a creative, advertising consent
    or an enabled placement this renders a placeholder that the stylesheet hides.
    """
    request = context.get("request")
    placement = AdPlacement.objects.filter(
        code=slot_code, is_active=True, is_enabled=True, is_deleted=False
    ).first()
    if not placement:
        return ""
    if not isinstance(request, HttpRequest):
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))
    if not AdsSettings.get_solo().ads_enabled or not has_consent(request, ADVERTISING):
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))
    creative = choose_creative(placement, {"consent_ads": True})
    creative_html = (
        render_creative(
            creative, click_href=zone_service.click_url(placement, creative)
        )
        if creative
        else ""
    )
    if creative_html:
        return format_html(
            '<div class="ad-slot ad-slot--{} {}" data-slot="{}">{}</div>',
            slot_code,
            css_class,
            slot_code,
            mark_safe(creative_html),
        )
    return mark_safe(_render_placeholder(slot_code, placement.name, css_class))


def _render_placeholder(slot_code: str, name: str, css_class: str) -> str:
    """Themed, stylesheet-hidden placeholder for an unfilled code-addressed slot."""
    escaped_code = escape(slot_code)
    return (
        f'<aside class="ad-placeholder ad-placeholder--{escaped_code} {escape(css_class)}" '
        f'data-slot="{escaped_code}" '
        f'aria-label="Advertisement: {escape(name)}">'
        f'<span class="ad-placeholder__disclosure">Advertisement</span>'
        f'<span class="ad-placeholder__message">Space available</span>'
        f"</aside>"
    )
