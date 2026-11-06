"""Small JPEG previews for image uploads."""

from __future__ import annotations

import io
import logging
from typing import IO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (320, 320)
THUMBNAIL_QUALITY = 80
WHITE = (255, 255, 255)


def make_thumbnail(source: IO[bytes]) -> bytes | None:
    """Return JPEG bytes for the image in ``source``, or ``None`` if it cannot be read.

    Animated images use their first frame; transparency is flattened onto white. The
    caller decides what a missing preview means (the UI falls back to an icon).
    """
    try:
        source.seek(0)
        with Image.open(source) as image:
            image.load()
            upright = ImageOps.exif_transpose(image)
            rgba = upright.convert("RGBA")
        flat = Image.new("RGB", rgba.size, WHITE)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        flat.thumbnail(THUMBNAIL_SIZE)
        buffer = io.BytesIO()
        flat.save(buffer, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        logger.warning("Thumbnail not created: %s", error)
        return None
    finally:
        source.seek(0)
    return buffer.getvalue()
