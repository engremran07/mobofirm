"""Template tags for rendering ad placements in public templates."""

from __future__ import annotations

import logging
import random
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.ads.constants import (
    LOCAL_DEMO_CAMPAIGN_NAME,
    LOCAL_DEMO_PLACEMENT_PREFIX,
)
from apps.ads.models import AdNetwork, AdPlacement, PlacementAssignment
from apps.ads.permissions import can_manage_ads

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

_URL_ATTRIBUTES = frozenset({"href", "src", "action", "formaction", "data", "srcset"})
_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:")


def _host_allowed(hostname: str) -> bool:
    return any(
        hostname == allowed or hostname.endswith("." + allowed)
        for allowed in _ALLOWED_AD_SCRIPT_DOMAINS
    )


class _CreativeAuditor(HTMLParser):
    """Collect policy violations from admin-authored creative markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        for name, value in values.items():
            if name.startswith("on") or name in {"style", "srcdoc"}:
                self.violations.append(f"forbidden attribute {name}")
            if name in _URL_ATTRIBUTES:
                compact = re.sub(r"[\x00-\x20]", "", value).lower()
                if compact.startswith(_DANGEROUS_SCHEMES):
                    self.violations.append(f"dangerous URL in {name}")
        if tag == "script":
            self._in_script = True
            parts = urlsplit(values.get("src", "").strip())
            if parts.scheme != "https" or not parts.hostname:
                self.violations.append("script without an absolute https src")
            elif not _host_allowed(parts.hostname.lower()):
                self.violations.append(f"script host not allowed: {parts.hostname}")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.violations.append("inline script content")


def _validate_ad_creative_code(code: str) -> bool:
    """Return True if code passes ad-creative allowlist validation.

    FINDING-0018: Admin-controlled creative HTML/script is parsed (not pattern
    matched) so a compromised or malicious staff member cannot inject arbitrary
    JavaScript that would run on every page viewed by end users (stored XSS).

    Rules:
    - script elements need an absolute https ``src`` on an allowlisted ad host
      and no inline body; protocol-relative and other schemes are rejected.
    - Event-handler, ``style`` and ``srcdoc`` attributes are rejected.
    - ``javascript:``, ``vbscript:`` and ``data:`` URLs are rejected.
    """
    if not code or not code.strip():
        return True

    auditor = _CreativeAuditor()
    auditor.feed(code)
    auditor.close()
    if auditor.violations:
        logger.warning("Ad creative rejected: %s", "; ".join(auditor.violations))
        return False
    return True


def _safe_click_url(url: str) -> str:
    """Return ``url`` only when it is an absolute http(s) URL."""
    parts = urlsplit((url or "").strip())
    return url.strip() if parts.scheme in {"http", "https"} and parts.netloc else ""


@register.simple_tag(takes_context=True)
def render_ad_slot(
    context: dict[str, object],
    slot_code: str,
    css_class: str = "",
    preview: bool = False,
) -> str:
    """Render an ad slot — real creative or themed placeholder.

    Usage::

        {% load ad_tags %}
        {% render_ad_slot "sidebar-top" %}
        {% render_ad_slot "in-article-top" css_class="my-6" %}
    """
    request = context.get("request")

    if preview:
        user = getattr(request, "user", None)
        if not can_manage_ads(user) or not slot_code.startswith(
            LOCAL_DEMO_PLACEMENT_PREFIX
        ):
            return ""
        placement = (
            AdPlacement.objects.filter(
                code=slot_code,
                is_active=False,
                is_enabled=False,
                is_deleted=False,
            )
            .only("pk", "name", "code", "allowed_sizes")
            .first()
        )
    else:
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

    if request is None:
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))

    if preview:
        assignments = list(
            PlacementAssignment.objects.filter(
                placement=placement,
                is_active=False,
                is_enabled=False,
                is_deleted=False,
                creative__is_active=False,
                creative__is_enabled=False,
                creative__is_deleted=False,
                creative__creative_type="html",
                creative__campaign__name=LOCAL_DEMO_CAMPAIGN_NAME,
                creative__campaign__type="house",
                creative__campaign__is_active=False,
                creative__campaign__is_deleted=False,
                creative__campaign__locked=True,
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
        creative_html = ""
        if assignments:
            chosen = _weighted_pick(assignments)
            creative_html = _render_creative(chosen.creative, preview=True)
        if not creative_html:
            return ""
        return mark_safe(
            f'<div class="ad-slot ad-slot--{escape(slot_code)} '
            f'{escape(css_class)}" data-slot="{escape(slot_code)}" '
            f'data-demo-preview="true">{creative_html}</div>'
        )

    from apps.users.services.consent import check as consent_check

    if not consent_check("ads", request):
        return mark_safe(_render_placeholder(slot_code, placement.name, css_class))

    # Check for assigned creatives with an active network
    has_network = AdNetwork.objects.filter(is_enabled=True, is_deleted=False).exists()

    creative_html = ""
    if has_network:
        assignments = list(
            PlacementAssignment.objects.filter(
                placement=placement,
                is_active=True,
                is_enabled=True,
                is_deleted=False,
                creative__is_active=True,
                creative__is_enabled=True,
                creative__is_deleted=False,
                creative__campaign__is_active=True,
                creative__campaign__is_deleted=False,
            )
            .select_related("creative", "creative__campaign")
            .only(
                "weight",
                "creative__campaign__is_active",
                "creative__campaign__start_at",
                "creative__campaign__end_at",
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
            if chosen.creative.campaign.is_live():
                creative_html = _render_creative(chosen.creative)

    if creative_html:
        return mark_safe(
            f'<div class="ad-slot ad-slot--{escape(slot_code)} {escape(css_class)}" '
            f'data-slot="{escape(slot_code)}">{creative_html}</div>'
        )

    # Placeholder
    return mark_safe(_render_placeholder(slot_code, placement.name, css_class))


def _weighted_pick(assignments: list[PlacementAssignment]) -> PlacementAssignment:
    """Weighted random pick from PlacementAssignment list."""
    weights = [a.weight for a in assignments]
    return random.choices(assignments, weights=weights, k=1)[0]


def _render_creative(creative: object, preview: bool = False) -> str:
    """Render a single AdCreative to HTML string.

    FINDING-0018: HTML and script creatives are validated before rendering.
    Invalid (potentially malicious) code returns an empty string and is logged.
    """
    from django.utils.html import escape

    ct = getattr(creative, "creative_type", "")
    html = getattr(creative, "html", "") or getattr(creative, "html_code", "")
    if ct == "html" and html:
        if preview and re.search(
            r"<\s*(script|iframe|img|video|audio|source|object|embed)\b|\bsrc\s*=",
            html,
            re.IGNORECASE,
        ):
            logger.warning(
                "Local ad preview rejected external or active media (id=%s)",
                getattr(creative, "pk", "?"),
            )
            return ""
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
        click = _safe_click_url(getattr(creative, "click_url", ""))
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
    escaped_code = escape(slot_code)
    escaped_css_class = escape(css_class)
    return (
        f'<aside class="ad-placeholder ad-placeholder--{escaped_code} {escaped_css_class}" '
        f'data-slot="{escaped_code}" '
        f'aria-label="Advertisement: {label}">'
        f'<span class="ad-placeholder__disclosure">Advertisement</span>'
        f'<span class="ad-placeholder__message">Space available</span>'
        f"</aside>"
    )
