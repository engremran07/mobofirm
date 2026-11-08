"""Template tags for rendering ad zones and code-addressed slots in public templates."""

from __future__ import annotations

import logging

from django import template
from django.http import HttpRequest
from django.utils.html import escape, format_html
from django.utils.safestring import SafeString, mark_safe

from apps.ads.models import AdPlacement, AdsSettings
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

__all__ = ["_safe_click_url", "_validate_ad_creative_code", "ad_zone", "render_ad_slot"]

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
