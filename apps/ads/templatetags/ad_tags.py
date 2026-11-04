"""Template tags for rendering ad placements in public templates."""

from __future__ import annotations

import logging
import random
import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()
logger = logging.getLogger(__name__)

# FINDING-0018: Allowed origins for ad script tags. Admin-authored `script`
# creatives may only reference these domains — anything else is rejected.
_ALLOWED_AD_SCRIPT_DOMAINS = frozenset(
    [
        "pagead2.googlesyndication.com",
        "adsby.googlesyndication.com",
        "cdn.media.net",
        "securepubads.g.doubleclick.net",
        "static.ezoiccdn.com",
        "ads.pubmatic.com",
        "s.adroll.com",
        "cdn.adnxs.com",
        "c.amazon-adsystem.com",
    ]
)

_ALLOWED_SCRIPT_SRC_RE = re.compile(
    r"""<script\b[^>]*\bsrc=["']https?://([^/"']+)""",
    re.IGNORECASE,
)


def _validate_ad_creative_code(code: str) -> bool:
    """Return True if code passes ad-creative allowlist validation.

    FINDING-0018: Admin-controlled creative HTML/script is validated to prevent
    a compromised or malicious staff member from injecting arbitrary JavaScript
    that would run on every page viewed by end users (stored XSS).

    Rules:
    - script tags must have a src= pointing to a known ad network domain.
    - Inline script without src is rejected.
    - Inline event handlers (onclick= etc.) in non-script elements are rejected.
    """
    if not code or not code.strip():
        return True

    stripped = code.strip()
    if re.search(r"""\bstyle\s*=""", stripped, re.IGNORECASE):
        logger.warning("Ad creative rejected: inline style attribute found")
        return False

    # Allow plain <img> / <a> / <ins> banners with no script elements
    if "<script" not in stripped.lower():
        # No script — still reject inline event handlers
        if re.search(r"""\bon\w+\s*=\s*["']?""", stripped, re.IGNORECASE):
            logger.warning("Ad creative rejected: inline event handler found")
            return False
        return True

    # Script element present — must have src pointing to an allowed domain
    for match in _ALLOWED_SCRIPT_SRC_RE.finditer(stripped):
        domain = match.group(1).lower()
        if not any(
            domain == allowed or domain.endswith("." + allowed)
            for allowed in _ALLOWED_AD_SCRIPT_DOMAINS
        ):
            logger.warning(
                "Ad creative rejected: script src domain not in allowlist: %s", domain
            )
            return False

    # Reject <script> blocks without src (inline scripts)
    if re.search(r"<script(?![^>]*\bsrc\s*=)[^>]*>", stripped, re.IGNORECASE):
        logger.warning("Ad creative rejected: inline script without src")
        return False

    return True


@register.simple_tag(takes_context=True)
def render_ad_slot(
    context: dict[str, object], slot_code: str, css_class: str = ""
) -> str:
    """Render an ad slot — real creative or themed placeholder.

    Usage::

        {% load ad_tags %}
        {% render_ad_slot "sidebar-top" %}
        {% render_ad_slot "in-article-top" css_class="my-6" %}
    """
    from apps.ads.models import AdNetwork, AdPlacement, PlacementAssignment

    placement = (
        AdPlacement.objects.filter(
            code=slot_code,
            is_active=True,
            is_enabled=True,
            is_deleted=False,
        )
        .only("pk", "name", "code", "allowed_sizes")
        .first()
    )
    if not placement:
        return ""

    request = context.get("request")
    if request is None:
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))  # noqa: S308

    from apps.users.services.consent import check as consent_check

    if not consent_check("ads", request):
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))  # noqa: S308

    # Check for assigned creatives with an active network
    has_network = AdNetwork.objects.filter(is_enabled=True, is_deleted=False).exists()

    creative_html = ""
    if has_network:
        assignments = list(
            PlacementAssignment.objects.filter(
                placement=placement,
                is_active=True,
                is_enabled=True,
                creative__is_active=True,
                creative__is_enabled=True,
                creative__is_deleted=False,
            )
            .select_related("creative")
            .only(
                "weight",
                "creative__html",
                "creative__html_code",
                "creative__image_url",
                "creative__click_url",
                "creative__creative_type",
                "creative__script_code",
            )
        )
        if assignments:
            chosen = _weighted_pick(assignments)
            creative_html = _render_creative(chosen.creative)  # type: ignore[attr-defined]

    if creative_html:
        return mark_safe(  # noqa: S308 — admin-controlled placement codes
            f'<div class="ad-slot ad-slot--{slot_code} {css_class}" '
            f'data-slot="{slot_code}">{creative_html}</div>'
        )

    # Placeholder
    return mark_safe(_render_placeholder(slot_code, placement.name, css_class))  # noqa: S308


def _weighted_pick(assignments: list) -> object:  # type: ignore[type-arg]
    """Weighted random pick from PlacementAssignment list."""
    weights = [a.weight for a in assignments]
    return random.choices(assignments, weights=weights, k=1)[0]  # noqa: S311


def _render_creative(creative: object) -> str:  # type: ignore[type-arg]
    """Render a single AdCreative to HTML string.

    FINDING-0018: HTML and script creatives are validated before rendering.
    Invalid (potentially malicious) code returns an empty string and is logged.
    """
    from django.utils.html import escape

    ct = getattr(creative, "creative_type", "")
    html = getattr(creative, "html", "") or getattr(creative, "html_code", "")
    if ct == "html" and html:
        # FINDING-0018: Validate admin-authored HTML against allowlist
        if not _validate_ad_creative_code(html):
            logger.error(
                "Ad creative HTML rejected by security validator (id=%s)",
                getattr(creative, "pk", "?"),
            )
            return ""
        return html
    if ct == "script":
        script = getattr(creative, "script_code", "")
        if script:
            # FINDING-0018: Validate script creative against domain allowlist
            if not _validate_ad_creative_code(script):
                logger.error(
                    "Ad creative script rejected by security validator (id=%s)",
                    getattr(creative, "pk", "?"),
                )
                return ""
            return script
    if ct == "banner":
        img = getattr(creative, "image_url", "")
        click = getattr(creative, "click_url", "")
        if img:
            alt = escape(getattr(creative, "name", "Ad"))
            if click:
                return (
                    f'<a href="{escape(click)}" target="_blank" rel="noopener sponsored">'
                    f'<img src="{escape(img)}" alt="{alt}" class="w-full rounded">'
                    f"</a>"
                )
            return f'<img src="{escape(img)}" alt="{alt}" class="w-full rounded">'
    return ""


def _render_placeholder(slot_code: str, name: str, css_class: str) -> str:
    """Render a themed placeholder box for an empty ad slot."""
    from django.utils.html import escape

    label = escape(name)
    return (
        f'<aside class="ad-placeholder ad-placeholder--{slot_code} {css_class}" '
        f'data-slot="{slot_code}" '
        f'aria-label="Advertisement: {label}">'
        f'<span class="ad-placeholder__disclosure">Advertisement</span>'
        f'<span class="ad-placeholder__message">Space available</span>'
        f"</aside>"
    )
