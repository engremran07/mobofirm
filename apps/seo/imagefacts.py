"""Facts about images served from this site (used by the Discover-readiness checks)."""

from __future__ import annotations

from pathlib import Path
from typing import IO
from urllib.parse import unquote, urljoin, urlsplit

from django.conf import settings as django_settings
from django.contrib.staticfiles import finders
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage
from PIL import Image, UnidentifiedImageError

from .siteurls import site_origin


def _width(opened: IO[bytes]) -> int:
    with Image.open(opened) as image:
        return int(image.width)


def local_image_width(url: str) -> int | None:
    """Pixel width of a site-hosted image in media or static storage, else ``None``.

    Remote images are never fetched here (outbound HTTP belongs to ``SafeFetcher``), so
    their size is unknown rather than guessed.
    """
    parts = urlsplit(url)
    if parts.scheme in {"http", "https"}:
        if parts.netloc.lower() != urlsplit(site_origin()).netloc.lower():
            return None
    elif parts.scheme or parts.netloc:
        return None
    path = parts.path
    media = urljoin("/", str(django_settings.MEDIA_URL))
    static = urljoin("/", str(django_settings.STATIC_URL))
    try:
        if path.startswith(media):
            with default_storage.open(unquote(path[len(media) :]), "rb") as handle:
                return _width(handle)
        if path.startswith(static):
            found = finders.find(unquote(path[len(static) :]))
            if isinstance(found, str):
                with Path(found).open("rb") as handle:
                    return _width(handle)
    except (SuspiciousFileOperation, OSError, ValueError, UnidentifiedImageError):
        return None
    return None
