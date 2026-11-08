"""Share cards and vertical videos for platforms that need media.

* Cards are drawn with Pillow (only if it is installed) in two sizes: 1200x630 and
  1080x1920.
* The slideshow video is a short vertical MP4 built from three Pillow frames by ``ffmpeg``
  when the binary is on ``PATH``. Without it, video platforms report "needs media" with the
  reason; nothing is faked.
* Files are written through Django's default storage under ``distribution/`` (the MEDIA
  location). Rows are only stored for files that exist, so installing ffmpeg later simply
  starts working; ``purge_expired`` removes files after the retention window.
"""

from __future__ import annotations

import hashlib
import io
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from apps.distribution import content as content_module
from apps.distribution.channels import MediaRefs
from apps.distribution.content import Content
from apps.distribution.models import MediaAsset
from apps.seo.siteurls import absolute_url

logger = logging.getLogger(__name__)

CARD_SIZE = (1200, 630)
VERTICAL_SIZE = (1080, 1920)
SLIDE_SECONDS = 3
FFMPEG_TIMEOUT = 180
BACKGROUND_TOP = (15, 23, 42)
BACKGROUND_BOTTOM = (30, 58, 138)
ACCENT = (56, 189, 248)
WHITE = (255, 255, 255)
MUTED = (203, 213, 225)


@dataclass(frozen=True, slots=True)
class MediaSpec:
    kind: str
    ref: str
    title: str
    label: str = ""
    lines: tuple[str, ...] = ()
    site: str = ""


@dataclass(frozen=True, slots=True)
class AssetResult:
    asset: MediaAsset | None
    reason: str = ""


def spec_from_content(item: Content, site: str) -> MediaSpec:
    """What the card says: title plus the facts that are safe to show publicly."""
    if item.kind == "blog":
        label = "Flashing guide" if item.is_guide else "New article"
        summary = content_module.teaser(item.summary, 140)
        return MediaSpec(
            item.kind, item.object_ref, item.title, label, (summary,), site
        )
    device = " ".join(p for p in (item.brand, item.model) if p) or item.title
    facts = (
        device,
        f"Android {item.android_version}" if item.android_version else "",
        f"Build {item.build_number}" if item.build_number else "",
        " ".join(item.chipset.split()[:3]),
    )
    return MediaSpec(
        item.kind,
        item.object_ref,
        item.title,
        "Firmware",
        tuple(line for line in facts if line),
        site,
    )


def spec_for(kind: str, ref: str, title: str, site: str) -> MediaSpec:
    """The spec for stored content; falls back to the title when it no longer loads."""
    item = content_module.load(kind, ref)
    if item is not None:
        return spec_from_content(item, site)
    return MediaSpec(kind, ref, title, "Share", (), site)


def pillow_available() -> bool:
    return find_spec("PIL") is not None


def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


def capabilities() -> dict[str, Any]:
    """What this server can generate (shown on the dashboard)."""
    return {
        "cards": pillow_available(),
        "video": pillow_available() and bool(ffmpeg_path()),
        "cards_note": "" if pillow_available() else "Pillow is not installed.",
        "video_note": (
            ""
            if ffmpeg_path()
            else "ffmpeg was not found on PATH, so no video is generated."
        ),
    }


def _font(size: int) -> Any:
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw: Any, text: str, font: Any, width: int, max_lines: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = [*lines[: max_lines - 1], lines[max_lines - 1] + "…"]
    return lines


def _gradient(size: tuple[int, int]) -> Any:
    width, height = size
    image = Image.new("RGB", size, BACKGROUND_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        mix = y / max(height - 1, 1)
        color = tuple(
            int(top + (bottom - top) * mix)
            for top, bottom in zip(BACKGROUND_TOP, BACKGROUND_BOTTOM, strict=True)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _draw_block(
    image: Any, spec: MediaSpec, lines: Sequence[str], *, title_size: int, margin: int
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle([0, 0, 14, height], fill=ACCENT)
    y = margin
    if spec.label:
        draw.text(
            (margin, y), spec.label.upper(), font=_font(title_size // 3), fill=ACCENT
        )
        y += title_size // 2
    font = _font(title_size)
    for line in _wrap(draw, spec.title, font, width - 2 * margin, 4):
        draw.text((margin, y), line, font=font, fill=WHITE)
        y += int(title_size * 1.2)
    y += title_size // 3
    small = _font(title_size // 2)
    for line in lines:
        for part in _wrap(draw, line, small, width - 2 * margin, 2):
            draw.text((margin, y), part, font=small, fill=MUTED)
            y += int(title_size * 0.7)
    if spec.site:
        draw.text(
            (margin, height - margin - title_size // 3),
            spec.site,
            font=_font(int(title_size // 2.2)),
            fill=WHITE,
        )


def render_card(spec: MediaSpec, size: tuple[int, int]) -> bytes:
    image = _gradient(size)
    vertical = size[1] > size[0]
    _draw_block(
        image,
        spec,
        spec.lines,
        title_size=88 if vertical else 62,
        margin=90 if vertical else 70,
    )
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _storage_name(spec: MediaSpec, asset_kind: str, extension: str) -> str:
    digest = hashlib.sha256(f"{spec.kind}:{spec.ref}".encode()).hexdigest()[:20]
    return f"distribution/{asset_kind}/{digest}.{extension}"


def _store(spec: MediaSpec, asset_kind: str, extension: str, data: bytes) -> MediaAsset:
    name = _storage_name(spec, asset_kind, extension)
    if default_storage.exists(name):
        default_storage.delete(name)
    saved = default_storage.save(name, ContentFile(data))
    asset, _created = MediaAsset.objects.update_or_create(
        content_kind=spec.kind,
        object_ref=spec.ref,
        asset_kind=asset_kind,
        defaults={"storage_name": saved, "reason": ""},
    )
    return asset


def _existing(spec: MediaSpec, asset_kind: str) -> MediaAsset | None:
    asset = MediaAsset.objects.filter(
        content_kind=spec.kind, object_ref=spec.ref, asset_kind=asset_kind
    ).first()
    if asset is not None and asset.ready and default_storage.exists(asset.storage_name):
        return asset
    return None


def ensure_card(spec: MediaSpec, *, vertical: bool = False) -> AssetResult:
    kind = MediaAsset.AssetKind.VERTICAL if vertical else MediaAsset.AssetKind.CARD
    found = _existing(spec, kind)
    if found is not None:
        return AssetResult(found)
    if not pillow_available():
        return AssetResult(
            None, "Pillow is not installed, so no share card is generated."
        )
    data = render_card(spec, VERTICAL_SIZE if vertical else CARD_SIZE)
    return AssetResult(_store(spec, kind, "png", data))


def _slides(spec: MediaSpec) -> list[bytes]:
    intro = MediaSpec(spec.kind, spec.ref, spec.title, spec.label, (), "")
    facts = MediaSpec(
        spec.kind,
        spec.ref,
        spec.title if not spec.lines else spec.lines[0],
        "Details",
        spec.lines[1:] or (),
        "",
    )
    outro = MediaSpec(
        spec.kind,
        spec.ref,
        f"Find it on {spec.site}" if spec.site else spec.title,
        "Read more",
        (),
        spec.site,
    )
    return [render_card(s, VERTICAL_SIZE) for s in (intro, facts, outro)]


def ensure_video(spec: MediaSpec) -> AssetResult:
    """A 9-second vertical slideshow (Shorts / Reels / TikTok); never faked."""
    found = _existing(spec, MediaAsset.AssetKind.VIDEO)
    if found is not None:
        return AssetResult(found)
    if not pillow_available():
        return AssetResult(
            None, "Pillow is not installed, so no video frames can be drawn."
        )
    binary = ffmpeg_path()
    if not binary:
        return AssetResult(
            None, "ffmpeg was not found on PATH, so no video is generated."
        )
    with tempfile.TemporaryDirectory(prefix="distribution-") as work:
        folder = Path(work)
        for index, frame in enumerate(_slides(spec), start=1):
            (folder / f"frame_{index}.png").write_bytes(frame)
        output = folder / "out.mp4"
        command = [
            binary,
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            f"1/{SLIDE_SECONDS}",
            "-i",
            str(folder / "frame_%d.png"),
            "-vf",
            "scale=1080:1920,format=yuv420p",
            "-c:v",
            "libx264",
            "-r",
            "30",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            run = subprocess.run(
                command, capture_output=True, timeout=FFMPEG_TIMEOUT, check=False
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            return AssetResult(None, f"ffmpeg could not run: {error}"[:300])
        if run.returncode != 0 or not output.exists():
            detail = run.stderr.decode(errors="replace").strip()[-200:]
            return AssetResult(None, f"ffmpeg failed: {detail}"[:300])
        data = output.read_bytes()
    return AssetResult(_store(spec, MediaAsset.AssetKind.VIDEO, "mp4", data))


def _read(name: str) -> bytes:
    with default_storage.open(name, "rb") as handle:
        return handle.read()


def _public(asset: MediaAsset | None) -> str:
    return (
        absolute_url(default_storage.url(asset.storage_name))
        if asset and asset.ready
        else ""
    )


def refs_for(kind: str, ref: str) -> MediaRefs:
    """Public URLs of the ready assets for one piece of content."""
    rows = {
        a.asset_kind: a
        for a in MediaAsset.objects.filter(content_kind=kind, object_ref=ref)
    }
    card = rows.get(MediaAsset.AssetKind.CARD)
    vertical = rows.get(MediaAsset.AssetKind.VERTICAL)
    video = rows.get(MediaAsset.AssetKind.VIDEO)
    return MediaRefs(
        image_url=_public(card),
        vertical_url=_public(vertical),
        video_url=_public(video),
        image_name=card.storage_name if card else "",
        video_name=video.storage_name if video else "",
        reader=_read,
    )


def purge_expired(days: int, now: datetime | None = None) -> int:
    """Delete assets older than ``days`` (0 keeps everything); returns the count."""
    if days <= 0:
        return 0
    cutoff = (now or timezone.now()) - timedelta(days=days)
    removed = 0
    for asset in MediaAsset.objects.filter(created_at__lt=cutoff):
        if asset.storage_name:
            try:
                default_storage.delete(asset.storage_name)
            except OSError:
                logger.warning("Could not delete %s", asset.storage_name)
                continue
        asset.delete()
        removed += 1
    return removed
