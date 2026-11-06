"""Field validators shared by the site settings models and admin forms."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import RegexValidator
from django.db.models.fields.files import FieldFile

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_VERIFICATION_FILE_BYTES = 256 * 1024
VERIFICATION_EXTENSIONS = frozenset({".txt", ".html", ".xml", ".json"})
# Extensions that may also be answered at the site root (Google, Bing and Yandex
# require the file there); ``.txt`` stays under /site/verification/ so the SEO
# app's own ``/<key>.txt`` ownership route is never shadowed.
ROOT_VERIFICATION_EXTENSIONS = frozenset({".html", ".xml", ".json"})
VERIFICATION_CONTENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".json": "application/json",
}
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

HEX_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
hex_color_validator = RegexValidator(
    regex=HEX_COLOR_PATTERN,
    message="Enter a six-digit hex colour such as #0b7376.",
)
ga4_id_validator = RegexValidator(
    regex=r"^G-[A-Z0-9]{4,20}$", message="Use a GA4 measurement ID such as G-ABC123XYZ."
)
gtm_id_validator = RegexValidator(
    regex=r"^GTM-[A-Z0-9]{4,12}$", message="Use a GTM container ID such as GTM-ABC123."
)
twitter_handle_validator = RegexValidator(
    regex=r"^[A-Za-z0-9_]{1,15}$",
    message="Enter the handle without @ (letters, digits and underscores).",
)
meta_name_validator = RegexValidator(
    regex=r"^[A-Za-z][A-Za-z0-9:._-]{0,99}$",
    message="Use the meta tag name only, for example google-site-verification.",
)
meta_content_validator = RegexValidator(
    regex=r"^[^<>\"'\r\n]{1,255}$",
    message="The content must be a single line without quotes or angle brackets.",
)


def validate_https_url(value: str) -> None:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc:
        raise ValidationError("Enter an absolute https:// URL.")


def validate_site_origin(value: str) -> None:
    """The public origin only: scheme and host, no path, query or fragment."""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValidationError("Enter an absolute http(s) origin.")
    if parts.path not in {"", "/"} or parts.query or parts.fragment or parts.username:
        raise ValidationError(
            "Use the origin only, for example https://example.com (no path or query)."
        )
    host = parts.hostname or ""
    local = host in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (local or settings.DEBUG):
        raise ValidationError("A public site URL must use https://.")


def validate_image_size(value: FieldFile) -> None:
    # Only freshly uploaded files are measured; stored files are not re-read.
    if not getattr(value, "_committed", True) and value.size > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"Images must be {MAX_IMAGE_BYTES // (1024 * 1024)} MiB or smaller."
        )


def validate_timezone_name(value: str) -> None:
    if not value:
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValidationError(f"{value!r} is not a known IANA time zone.") from exc


def validate_language_code(value: str) -> None:
    if value and value not in {code for code, _name in settings.LANGUAGES}:
        raise ValidationError(f"{value!r} is not one of the configured languages.")


def validate_verification_filename(value: str) -> None:
    if not SAFE_FILENAME.fullmatch(value) or ".." in value:
        raise ValidationError(
            "Use letters, digits, dots, hyphens and underscores only (no paths)."
        )
    if PurePosixPath(value).suffix.lower() not in VERIFICATION_EXTENSIONS:
        raise ValidationError(
            "Allowed extensions: " + ", ".join(sorted(VERIFICATION_EXTENSIONS)) + "."
        )


def validate_verification_upload(value: FieldFile | UploadedFile) -> None:
    if getattr(value, "_committed", False):
        return
    if value.size is not None and value.size > MAX_VERIFICATION_FILE_BYTES:
        raise ValidationError(
            f"Verification files must be {MAX_VERIFICATION_FILE_BYTES // 1024} KiB "
            "or smaller."
        )


def _linear(channel: int) -> float:
    srgb = channel / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def contrast_against_white(hex_color: str) -> float:
    """WCAG contrast ratio of ``#rrggbb`` text on a white background."""
    red, green, blue = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)
    return 1.05 / (luminance + 0.05)
