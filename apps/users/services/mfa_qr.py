"""Server-rendered QR code for the authenticator enrolment page (inline SVG)."""

from __future__ import annotations

import segno
from django.utils.html import format_html
from django.utils.safestring import SafeString

QR_CLASS = "mfa-qr"
QR_LABEL = "QR code for your authenticator app"


def render_qr_svg(data: str) -> SafeString:
    """Inline SVG of ``data`` with an accessible name and no style attributes.

    The markup is built by segno from the otpauth URI only; colours are SVG
    presentation attributes, so it is CSP-safe (no inline ``style``).
    """
    code = segno.make(data, error="m")
    svg = code.svg_inline(
        scale=5,
        border=2,
        dark="#000000",
        light="#ffffff",
        svgclass=QR_CLASS,
        lineclass=None,
        omitsize=True,
    )
    prefix = "<svg "
    if not svg.startswith(prefix):
        raise ValueError("Unexpected SVG output from segno")
    labelled = format_html(
        '<svg role="img" aria-label="{}" focusable="false" ', QR_LABEL
    )
    return SafeString(f"{labelled}{svg[len(prefix) :]}")
