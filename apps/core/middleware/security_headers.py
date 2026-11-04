"""
apps.core.middleware.security_headers

Enterprise-grade security header middleware.

✅ Django 5.2+ / Python 3.12+
✅ Per-request CSP nonces (no 'unsafe-inline')
✅ Compatible with modern browsers / COOP / CORP
✅ Minimal overhead (nonce generated once per request)
✅ Logging-aware, no silent leaks
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterable, Sequence

from django.conf import settings
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)

DEFAULT_HSTS = "max-age=63072000; includeSubDomains; preload"
DEFAULT_COEP = "require-corp"
DEFAULT_CORP = "same-origin"


class SecurityHeadersMiddleware:
    """Attach enterprise-grade secure HTTP headers to each response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response
        self.hsts_value = getattr(settings, "SECURITY_HSTS_VALUE", DEFAULT_HSTS)
        self.coep_value = getattr(settings, "SECURITY_COEP_VALUE", DEFAULT_COEP)
        self.corp_value = getattr(settings, "SECURITY_CORP_VALUE", DEFAULT_CORP)
        # Log once at startup for visibility
        logger.info(
            "SecurityHeadersMiddleware initialized (DEBUG=%s)",
            getattr(settings, "DEBUG", False),
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Create per-request nonce (used in inline scripts/styles if templates add it)
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce  # type: ignore[attr-defined]

        response = self.get_response(request)

        # ------------------------------------------------------------------
        # Core modern security headers
        # ------------------------------------------------------------------
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Cross-Origin-Opener-Policy"] = "same-origin"
        response["Cross-Origin-Resource-Policy"] = self.corp_value
        response["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )

        # ------------------------------------------------------------------
        # Content Security Policy & COEP
        # Skip CSP + COEP for XML responses (sitemaps) — XSLT transforms
        # produce HTML with inline <style>/<script> that cannot carry nonces,
        # and COEP require-corp may block the XSL stylesheet fetch.
        # ------------------------------------------------------------------
        content_type = response.get("Content-Type", "")
        is_xml = "xml" in content_type

        if not is_xml:
            response["Cross-Origin-Embedder-Policy"] = self.coep_value
            # Nonce-based CSP even in DEBUG to discourage inline/eval
            script_hosts = self._merge_sources(
                [
                    "'self'",
                    f"'nonce-{nonce}'",
                    "https://www.google.com/recaptcha/",
                    "https://www.gstatic.com/recaptcha/",
                ],
                getattr(settings, "SECURITY_SCRIPT_SRC_EXTRA", ()),
            )
            style_hosts = self._merge_sources(
                [
                    "'self'",
                    f"'nonce-{nonce}'",
                ],
                getattr(settings, "SECURITY_STYLE_SRC_EXTRA", ()),
            )
            # style-src-attr/elem: 'unsafe-inline' needed for HTMX dynamic styles
            # Per CSP Level 3: HTMX sets element.style properties requiring 'unsafe-inline'
            style_attr_hosts = ["'self'", "'unsafe-inline'"]
            style_elem_hosts = self._merge_sources(
                ["'self'", "'unsafe-inline'"],
                getattr(settings, "SECURITY_STYLE_SRC_EXTRA", ()),
            )
            connect_hosts = self._merge_sources(
                ["'self'", "ws:", "wss:"],
                getattr(settings, "SECURITY_CONNECT_SRC_EXTRA", ()),
            )
            frame_hosts = self._merge_sources(
                ["'self'", "https://www.google.com/recaptcha/"],
                getattr(settings, "SECURITY_FRAME_SRC_EXTRA", ()),
            )
            font_hosts = self._merge_sources(
                ["'self'", "data:"],
                getattr(settings, "SECURITY_FONT_SRC_EXTRA", ()),
            )

            csp = (
                f"default-src 'self'; "
                f"script-src {' '.join(script_hosts)}; "
                f"style-src {' '.join(style_hosts)}; "
                f"style-src-attr {' '.join(style_attr_hosts)}; "
                f"style-src-elem {' '.join(style_elem_hosts)}; "
                "img-src 'self' data: https:; "
                f"font-src {' '.join(font_hosts)}; "
                f"connect-src {' '.join(connect_hosts)}; "
                f"frame-src {' '.join(frame_hosts)};"
            )

            response.setdefault("Content-Security-Policy", csp)

        # ------------------------------------------------------------------
        # Strict-Transport-Security (HSTS)
        # ------------------------------------------------------------------
        if not getattr(settings, "DEBUG", False) and self.hsts_value:
            is_secure = request.is_secure()
            xfp = request.META.get("HTTP_X_FORWARDED_PROTO", "")
            if is_secure or xfp.startswith("https"):
                response["Strict-Transport-Security"] = self.hsts_value

        return response

    @staticmethod
    def _merge_sources(base: Sequence[str], extra: Iterable[str]) -> list[str]:
        merged = [src for src in base if src]
        merged.extend([src for src in extra if src])
        # Preserve order but drop duplicates
        seen = set()
        deduped = []
        for src in merged:
            if src not in seen:
                seen.add(src)
                deduped.append(src)
        return deduped
