"""Upload validation: name sanitising, extension allow-list and content sniffing."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass

from django.core.files.uploadedfile import UploadedFile
from PIL import Image, UnidentifiedImageError

from . import conf
from .models import FileType


class UploadRejected(ValueError):
    """Raised when an upload fails validation; the message is safe to show users."""


EXTENSION_TYPES: dict[str, FileType] = {
    **dict.fromkeys(("png", "jpg", "jpeg", "gif", "webp"), FileType.IMAGE),
    "mp4": FileType.VIDEO,
    "mp3": FileType.AUDIO,
    **dict.fromkeys(
        ("pdf", "txt", "md", "csv", "json", "log", "docx", "xlsx", "pptx"),
        FileType.DOCUMENT,
    ),
    **dict.fromkeys(
        ("zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz"), FileType.ARCHIVE
    ),
    **dict.fromkeys(("img", "bin", "apk"), FileType.OTHER),
}
TEXT_EXTENSIONS = frozenset({"txt", "md", "csv", "json", "log"})
ZIP_EXTENSIONS = frozenset({"zip", "docx", "xlsx", "pptx", "apk"})
SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    "7z": (b"7z\xbc\xaf\x27\x1c",),
    "rar": (b"Rar!\x1a\x07",),
    "gz": (b"\x1f\x8b",),
    "tgz": (b"\x1f\x8b",),
    "bz2": (b"BZh",),
    "xz": (b"\xfd7zXZ\x00",),
    "mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
}
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
SNIFF_BYTES = 8192
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")


@dataclass(frozen=True)
class ValidatedUpload:
    name: str
    extension: str
    file_type: FileType
    mime_type: str
    size: int
    sha256: str


def sanitize_filename(raw_name: str) -> str:
    name = _UNSAFE_NAME.sub("_", raw_name.replace("\\", "/").rsplit("/", 1)[-1])
    name = name.strip().lstrip(".")
    if not name:
        raise UploadRejected("The file needs a valid name.")
    return name[:255]


def _read_head(uploaded: UploadedFile) -> bytes:
    uploaded.seek(0)
    head = uploaded.read(SNIFF_BYTES)
    uploaded.seek(0)
    return head


def _check_content(extension: str, head: bytes, uploaded: UploadedFile) -> None:
    mismatch = UploadRejected("The file content does not match its extension.")
    if extension in ZIP_EXTENSIONS and not head.startswith(ZIP_SIGNATURES):
        raise mismatch
    if extension == "mp4" and head[4:8] != b"ftyp":
        raise mismatch
    signatures = SIGNATURES.get(extension)
    if signatures and not head.startswith(signatures):
        raise mismatch
    if extension == "tar" and head[257:262] != b"ustar":
        raise mismatch
    if extension in TEXT_EXTENSIONS:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as exc:
            if exc.start < len(head) - 4:
                raise mismatch from exc
        if b"\x00" in head:
            raise mismatch
    if extension in IMAGE_EXTENSIONS:
        try:
            uploaded.seek(0)
            with Image.open(uploaded) as image:
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError) as exc:
            raise mismatch from exc
        finally:
            uploaded.seek(0)


def validate_upload(uploaded: UploadedFile) -> ValidatedUpload:
    name = sanitize_filename(uploaded.name or "")
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in EXTENSION_TYPES:
        raise UploadRejected(
            f"Files of type “.{extension or 'unknown'}” are not allowed."
        )
    size = uploaded.size or 0
    if size <= 0:
        raise UploadRejected("The file is empty.")
    if size > conf.max_upload_bytes():
        raise UploadRejected("The file exceeds the maximum upload size.")

    _check_content(extension, _read_head(uploaded), uploaded)

    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return ValidatedUpload(
        name=name,
        extension=extension,
        file_type=EXTENSION_TYPES[extension],
        mime_type=mime_type,
        size=size,
        sha256=digest.hexdigest(),
    )
