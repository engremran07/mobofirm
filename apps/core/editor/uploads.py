"""Validation and re-encoding of images uploaded through the editor.

Nothing the client sends is trusted: the format is sniffed from the bytes by Pillow
(never from the file name or content type), the pixel count is checked before the
image is decoded, orientation is baked in, and the image is re-encoded from its pixels
only, which drops EXIF/GPS, ICC and any appended payload. Names are random.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Final

from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS: Final = 36_000_000
MAX_EDGE: Final = 2000
JPEG_QUALITY: Final = 85
ENCODINGS: Final[dict[str, tuple[str, str]]] = {
    "JPEG": ("JPEG", ".jpg"),
    "PNG": ("PNG", ".png"),
    "WEBP": ("WEBP", ".webp"),
    "GIF": ("PNG", ".png"),
}


class UploadRejectedError(ValueError):
    """The upload failed validation; the message is safe to show to the user."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """A sanitised image ready to be stored."""

    content: bytes
    extension: str
    width: int
    height: int


def _read_bounded(upload: UploadedFile, max_bytes: int) -> bytes:
    if upload.size is not None and upload.size > max_bytes:
        raise UploadRejectedError("The image is larger than allowed.", "too_large")
    data = upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadRejectedError("The image is larger than allowed.", "too_large")
    if not data:
        raise UploadRejectedError("The file is empty.", "empty")
    return data


def _open(data: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        image = Image.open(BytesIO(data))
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise UploadRejectedError("This is not a valid image.", "invalid") from error
    if image.format not in ENCODINGS:
        raise UploadRejectedError(
            "Only JPEG, PNG, WebP and GIF images are accepted.", "format"
        )
    width, height = image.size
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise UploadRejectedError("The image dimensions are too large.", "dimensions")
    return image


def _encode(image: Image.Image, target: str) -> bytes:
    buffer = BytesIO()
    if target == "JPEG":
        image.convert("RGB").save(
            buffer, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True
        )
    elif target == "WEBP":
        image.save(buffer, "WEBP", quality=JPEG_QUALITY)
    else:
        mode = (
            "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
        )
        image.convert(mode).save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def process_image(upload: UploadedFile, max_kb: int) -> ProcessedImage:
    """Validate ``upload`` and return a metadata-free, size-bounded re-encoding."""
    data = _read_bounded(upload, max_kb * 1024)
    image = _open(data)
    target, extension = ENCODINGS[str(image.format)]
    try:
        image.load()
        image = ImageOps.exif_transpose(image)
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        content = _encode(image, target)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise UploadRejectedError("This is not a valid image.", "invalid") from error
    return ProcessedImage(content, extension, image.width, image.height)
