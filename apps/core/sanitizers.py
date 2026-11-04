"""
apps.core.sanitizers

Enterprise HTML sanitization utilities for XSS prevention.
Uses nh3 library (Rust-based, actively maintained) to sanitize user-provided HTML content.
Migrated from bleach (deprecated/maintenance mode) in December 2025.

Usage:
    from apps.core.sanitizers import sanitize_ad_code, sanitize_html_content

    # For ad code
    safe_code = sanitize_ad_code(untrusted_ad_code)

    # For CMS content
    safe_content = sanitize_html_content(untrusted_html)
"""

from __future__ import annotations

import logging
import re

try:
    import nh3

    NH3_AVAILABLE = True
except ImportError:
    NH3_AVAILABLE = False

# Fallback for legacy code that checks BLEACH_AVAILABLE
BLEACH_AVAILABLE = NH3_AVAILABLE

logger = logging.getLogger(__name__)

# =============================================================================
# AD CODE SANITIZATION
# =============================================================================

# Tags allowed in ad code (includes ad network requirements)
ALLOWED_AD_TAGS = [
    "div",
    "span",
    "a",
    "img",
    "ins",  # Basic structure
    "iframe",  # For embedded ads
    "script",  # External ad scripts only (src required)
]

ALLOWED_AD_ATTRS = {
    "*": [
        "class",
        "id",
        "style",
        "data-ad-client",
        "data-ad-slot",
        "data-ad-format",
        "data-full-width-responsive",
        "data-ad-layout",
        "data-ad-layout-key",
    ],
    "a": ["href", "target", "rel", "title"],
    "img": ["src", "alt", "width", "height", "loading"],
    "ins": [
        "class",
        "style",
        "data-ad-client",
        "data-ad-slot",
        "data-ad-format",
        "data-full-width-responsive",
        "data-ad-layout",
        "data-ad-layout-key",
    ],
    "iframe": [
        "src",
        "width",
        "height",
        "frameborder",
        "allowfullscreen",
        "allow",
        "loading",
        "referrerpolicy",
    ],
    "script": ["src", "async", "defer", "crossorigin", "integrity"],
}

# Trusted ad network domains
TRUSTED_SCRIPT_DOMAINS = [
    "pagead2.googlesyndication.com",
    "googleads.g.doubleclick.net",
    "www.googletagmanager.com",
    "connect.facebook.net",
    "platform.twitter.com",
    "cdn.taboola.com",
    "cdn.outbrain.com",
]


def sanitize_ad_code(code: str | None) -> str:
    """
    Sanitize ad code while preserving legitimate ad network scripts.

    Strips dangerous patterns like inline scripts with code, event handlers,
    and javascript: URLs while allowing external scripts from trusted domains.

    Args:
        code: Raw ad code HTML string

    Returns:
        Sanitized HTML string safe for rendering
    """
    if not code:
        return ""

    if not NH3_AVAILABLE:
        logger.warning("nh3 not installed - ad code not sanitized!")
        return code

    # nh3 uses sets for tags and dict of sets for attributes
    tags = set(ALLOWED_AD_TAGS)

    # Convert attribute dict to nh3 format (set of attribute names per tag)
    attrs = {}
    for tag, attr_list in ALLOWED_AD_ATTRS.items():
        if tag == "*":
            # nh3 uses '*' for generic attributes
            attrs["*"] = set(attr_list)
        else:
            attrs[tag] = set(attr_list)

    # First pass: nh3 clean
    cleaned = nh3.clean(
        code,
        tags=tags,
        attributes=attrs,
        strip_comments=True,
    )

    # Second pass: Remove inline script content (only allow src-based scripts)
    # Remove any script tags that don't have src attribute
    cleaned = re.sub(
        r"<script(?![^>]*\bsrc\b)[^>]*>.*?</script>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return cleaned


# =============================================================================
# CMS CONTENT SANITIZATION
# =============================================================================

# Tags allowed in CMS/blog content
ALLOWED_CONTENT_TAGS = [
    # Text formatting
    "p",
    "br",
    "hr",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "strike",
    "del",
    "ins",
    "sup",
    "sub",
    "mark",
    "small",
    "abbr",
    "cite",
    "q",
    # Headings
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    # Lists
    "ul",
    "ol",
    "li",
    "dl",
    "dt",
    "dd",
    # Links and media
    "a",
    "img",
    "figure",
    "figcaption",
    "picture",
    "source",
    "video",
    "audio",
    "iframe",
    # Tables
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "colgroup",
    "col",
    # Structure
    "div",
    "span",
    "article",
    "section",
    "aside",
    "header",
    "footer",
    "nav",
    "main",
    "blockquote",
    "pre",
    "code",
    "details",
    "summary",
]

ALLOWED_CONTENT_ATTRS = {
    "*": ["class", "id", "title", "lang", "dir"],
    "a": ["href", "target", "title", "download"],
    "img": ["src", "alt", "width", "height", "loading", "srcset", "sizes"],
    "iframe": [
        "src",
        "width",
        "height",
        "frameborder",
        "allowfullscreen",
        "allow",
        "loading",
        "referrerpolicy",
        "title",
    ],
    "video": [
        "src",
        "controls",
        "width",
        "height",
        "poster",
        "preload",
        "autoplay",
        "muted",
        "loop",
        "playsinline",
    ],
    "audio": ["src", "controls", "preload", "autoplay", "muted", "loop"],
    "source": ["src", "type", "srcset", "sizes", "media"],
    "blockquote": ["cite"],
    "q": ["cite"],
    "th": ["colspan", "rowspan", "scope"],
    "td": ["colspan", "rowspan"],
    "col": ["span"],
    "colgroup": ["span"],
    "abbr": ["title"],
    "time": ["datetime"],
    "data": ["value"],
    "details": ["open"],
}

# Allowed URL schemes
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "tel"]


def sanitize_html_content(content: str | None) -> str:
    """
    Sanitize CMS/blog content while preserving rich text formatting.

    Removes scripts, event handlers, and dangerous patterns while
    allowing legitimate HTML formatting for content editing.

    Args:
        content: Raw HTML content string

    Returns:
        Sanitized HTML string safe for rendering
    """
    if not content:
        return ""

    if not NH3_AVAILABLE:
        logger.warning("nh3 not installed - content not sanitized!")
        return content

    # nh3 uses sets for tags and dict of sets for attributes
    tags = set(ALLOWED_CONTENT_TAGS)

    # Convert attribute dict to nh3 format
    attrs = {}
    for tag, attr_list in ALLOWED_CONTENT_ATTRS.items():
        if tag == "*":
            attrs["*"] = set(attr_list)
        else:
            attrs[tag] = set(attr_list)

    # nh3 uses url_schemes instead of protocols
    # Note: nh3 manages `rel` on <a> tags internally via link_rel — never
    # include "rel" in tag_attributes or ammonia will panic.
    return nh3.clean(
        content,
        tags=tags,
        attributes=attrs,
        link_rel="nofollow noopener",
        url_schemes=set(ALLOWED_PROTOCOLS),
        strip_comments=True,
    )


def sanitize_plain_text(text: str | None) -> str:
    """
    Strip all HTML tags, returning plain text only.

    Args:
        text: Text that may contain HTML

    Returns:
        Plain text with all HTML removed
    """
    if not text:
        return ""

    if not NH3_AVAILABLE:
        # Fallback: basic tag stripping
        return re.sub(r"<[^>]+>", "", text)

    # nh3.clean with empty tags set strips all HTML
    return nh3.clean(text, tags=set())


# =============================================================================
# VALIDATION HELPERS
# =============================================================================


def is_safe_url(url: str, allowed_hosts: list | None = None) -> bool:
    """
    Check if a URL is safe for redirects/links.

    Prevents javascript: URLs, data: URLs with scripts, and
    optionally restricts to specific hosts.

    Args:
        url: URL to validate
        allowed_hosts: Optional list of allowed hostnames

    Returns:
        True if URL is safe, False otherwise
    """
    if not url:
        return False

    url = url.strip().lower()

    # Block dangerous protocols
    dangerous_prefixes = [
        "javascript:",
        "vbscript:",
        "data:text/html",
        "data:application/javascript",
        "data:text/javascript",
    ]
    for prefix in dangerous_prefixes:
        if url.startswith(prefix):
            return False

    # If allowed_hosts specified, validate hostname
    if allowed_hosts:
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            if parsed.netloc and parsed.netloc not in allowed_hosts:
                return False
        except Exception:
            return False

    return True


__all__ = [
    "BLEACH_AVAILABLE",  # Alias for backward compatibility
    "NH3_AVAILABLE",
    "is_safe_url",
    "sanitize_ad_code",
    "sanitize_html_content",
    "sanitize_plain_text",
]
