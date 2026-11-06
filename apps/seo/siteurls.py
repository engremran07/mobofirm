"""Absolute URL helpers: canonical URLs are built from the site settings' URL."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from django.http import HttpRequest

from apps.site_settings.services import site_origin as configured_site_origin


def site_origin(request: HttpRequest | None = None) -> str:
    """Scheme and host of the public site; the configured site URL wins."""
    return configured_site_origin(request)


def is_absolute_http(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def absolute_url(value: str, request: HttpRequest | None = None) -> str:
    """Make a site path absolute; leave absolute http(s) URLs untouched."""
    if not value:
        return ""
    if is_absolute_http(value):
        return value
    origin = site_origin(request)
    if not origin or not value.startswith("/"):
        return ""
    return f"{origin}{value}"


def same_site(url: str, request: HttpRequest | None = None) -> bool:
    origin = urlsplit(site_origin(request))
    parts = urlsplit(url)
    return bool(origin.netloc) and parts.netloc.lower() == origin.netloc.lower()


def same_site_image(url: str) -> str:
    """A site-served image path; remote hosts are not advertised as page images."""
    return url if url.startswith("/") and not url.startswith("//") else ""


def path_and_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("", "", parts.path or "/", parts.query, ""))
