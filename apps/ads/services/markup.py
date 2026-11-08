"""Validation and rendering of admin-authored creative markup.

FINDING-0018: HTML and script creatives are parsed (not pattern matched) so a compromised
or careless staff member cannot inject arbitrary JavaScript that would run on every page
read by visitors (stored XSS).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.utils.html import escape

from apps.ads.models import AdCreative

logger = logging.getLogger(__name__)

# Origins an admin-authored `script` creative may reference. The browser's CSP still has
# to allow the host (CSP_AD_HOSTS); anything outside this list is rejected up front.
ALLOWED_AD_SCRIPT_DOMAINS = frozenset(
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
        for allowed in ALLOWED_AD_SCRIPT_DOMAINS
    )


class _CreativeAuditor(HTMLParser):
    """Collect policy violations from admin-authored creative markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []
        self._in_script = False

    def handle_starttag(
        self, tag: str, attrs: Sequence[tuple[str, str | None]]
    ) -> None:
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

    def handle_startendtag(
        self, tag: str, attrs: Sequence[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.violations.append("inline script content")


def validate_ad_creative_code(code: str) -> bool:
    """True when ``code`` passes the creative allowlist.

    - script elements need an absolute https ``src`` on an allowlisted host and no body;
    - event-handler, ``style`` and ``srcdoc`` attributes are rejected;
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


def safe_click_url(url: str) -> str:
    """Return ``url`` only when it is an absolute http(s) URL."""
    parts = urlsplit((url or "").strip())
    return url.strip() if parts.scheme in {"http", "https"} and parts.netloc else ""


def render_creative(creative: AdCreative, *, click_href: str = "") -> str:
    """Render one creative to HTML; invalid markup renders as an empty string.

    ``click_href`` replaces the destination of banner links (the click-tracking
    redirect); without it the creative's own click URL is used.
    """
    kind = creative.creative_type
    html = creative.html or creative.html_code
    if kind in {"html", "native"} and html:
        if not validate_ad_creative_code(html):
            logger.error("Ad creative HTML rejected by validator (id=%s)", creative.pk)
            return ""
        return html
    if kind == "script" and creative.script_code:
        if not validate_ad_creative_code(creative.script_code):
            logger.error(
                "Ad creative script rejected by validator (id=%s)", creative.pk
            )
            return ""
        return creative.script_code
    if kind == "banner" and creative.image_url:
        destination = click_href or safe_click_url(creative.click_url)
        image = (
            f'<img class="ad-zone__img" src="{escape(creative.image_url)}" '
            f'alt="{escape(creative.name)}" loading="lazy" decoding="async">'
        )
        if destination:
            return (
                f'<a href="{escape(destination)}" target="_blank" '
                f'rel="noopener sponsored nofollow">{image}</a>'
            )
        return image
    return ""
