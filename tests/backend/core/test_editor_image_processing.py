"""Pillow validation and re-encoding of editor uploads (no database needed)."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.core.editor.uploads import UploadRejectedError, process_image


def _image_bytes(fmt: str, size: tuple[int, int] = (40, 30), **save: object) -> bytes:
    buffer = BytesIO()
    mode = "RGBA" if fmt in {"PNG", "GIF", "WEBP"} else "RGB"
    Image.new(mode, size, (200, 10, 10, 255)[: len(mode)]).save(buffer, fmt, **save)
    return buffer.getvalue()


def _upload(data: bytes, name: str = "x.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="image/png")


@pytest.mark.parametrize(
    ("fmt", "extension"),
    [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp"), ("GIF", ".png")],
)
def test_accepted_formats_are_reencoded(fmt: str, extension: str) -> None:
    """Allowed formats come back re-encoded (GIF flattens to PNG)."""
    result = process_image(_upload(_image_bytes(fmt)), 512)
    assert result.extension == extension
    assert (result.width, result.height) == (40, 30)
    assert Image.open(BytesIO(result.content)).size == (40, 30)


def test_exif_and_metadata_are_stripped() -> None:
    """GPS/EXIF data never survives the re-encode."""
    source = Image.new("RGB", (20, 20), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    exif[0x8825] = {1: "N"}
    buffer = BytesIO()
    source.save(buffer, "JPEG", exif=exif)
    assert b"SecretCamera" in buffer.getvalue()
    cleaned = process_image(_upload(buffer.getvalue(), "p.jpg"), 512).content
    assert b"SecretCamera" not in cleaned
    assert not Image.open(BytesIO(cleaned)).getexif()


def test_large_images_are_downscaled() -> None:
    """Edges are capped at 2000 px."""
    result = process_image(_upload(_image_bytes("PNG", (3000, 1500))), 4096)
    assert (result.width, result.height) == (2000, 1000)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"", "empty"),
        (b"GIF89a-not-really", "invalid"),
        (
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>",
            "invalid",
        ),
        (b"<?php echo 1; ?>", "invalid"),
    ],
)
def test_non_images_are_rejected(payload: bytes, code: str) -> None:
    """Anything Pillow cannot identify as a raster image is refused."""
    with pytest.raises(UploadRejectedError) as error:
        process_image(_upload(payload, "evil.png"), 512)
    assert error.value.code == code


def test_unsupported_formats_are_rejected() -> None:
    """Real images in other formats (BMP, TIFF) are not accepted."""
    for fmt in ("BMP", "TIFF"):
        with pytest.raises(UploadRejectedError) as error:
            process_image(_upload(_image_bytes(fmt)), 512)
        assert error.value.code == "format"


def test_size_limit_is_enforced_before_decoding() -> None:
    """Files above the profile limit are refused."""
    data = _image_bytes("PNG", (400, 400), compress_level=0)
    with pytest.raises(UploadRejectedError) as error:
        process_image(_upload(data), 1)
    assert error.value.code == "too_large"


def test_pixel_bombs_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dimensions above the pixel ceiling are refused before decoding."""
    monkeypatch.setattr("apps.core.editor.uploads.MAX_PIXELS", 100)
    with pytest.raises(UploadRejectedError) as error:
        process_image(_upload(_image_bytes("PNG", (40, 30))), 512)
    assert error.value.code == "dimensions"


def test_truncated_images_are_rejected() -> None:
    """A cut-off file fails cleanly instead of raising."""
    data = _image_bytes("PNG", (200, 200))
    with pytest.raises(UploadRejectedError):
        process_image(_upload(data[: len(data) // 2]), 512)
