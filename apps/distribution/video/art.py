"""Generated art: a QR code and a locally stored image, never anything fetched or hotlinked."""

from __future__ import annotations

import base64
import io
import logging
from urllib.parse import unquote, urlsplit

import segno
from django.conf import settings
from django.core.files.storage import default_storage
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_SIDE = 1400
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


def qr_path(url: str) -> dict[str, object]:
    """A QR code as one SVG path (module size 1) plus the matrix size."""
    code = segno.make(url, error="m", micro=False)
    matrix = [list(row) for row in code.matrix]
    parts: list[str] = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            start = x
            while x < len(row) and row[x]:
                x += 1
            parts.append(f"M{start + 0} {y}h{x - start}v1h-{x - start}z")
    return {"size": len(matrix), "d": "".join(parts)}


def local_image(url: str) -> str:
    """A data URI for an image served from this site's media storage, or ``''``.

    Only paths below ``MEDIA_URL`` are read, through the storage backend; the bytes are
    verified and re-encoded with Pillow so a hostile file cannot reach the render page.
    """
    if not url:
        return ""
    prefix = str(getattr(settings, "MEDIA_URL", "/media/"))
    path = urlsplit(url).path
    if not path.startswith(prefix) or ".." in path:
        return ""
    name = unquote(path[len(prefix) :])
    try:
        with default_storage.open(name, "rb") as handle:
            raw = handle.read(MAX_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return ""
    if len(raw) > MAX_IMAGE_BYTES:
        return ""
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in ALLOWED_FORMATS:
            return ""
        image.load()
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=88)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        logger.warning("Ignoring an unreadable image for a video")
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
