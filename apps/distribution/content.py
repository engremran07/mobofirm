"""What gets shared: one neutral ``Content`` value built from firmware or a blog post.

Reaching into the firmware/blog apps is done through ``django.apps`` model lookup and
public URL names only (no cross-app imports). Gated content (paid / featured firmware,
featured / paid posts) is reduced to a teaser: title and a short summary, never the body
or a download link.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.distribution.constants import CONTENT_BLOG, CONTENT_FIRMWARE, TRENDING_DAYS
from apps.seo.siteurls import absolute_url

logger = logging.getLogger(__name__)
TEASER_CHARS = 140


@dataclass(frozen=True, slots=True)
class Content:
    """What to share. ``object_ref`` identifies the object for idempotency."""

    kind: str
    object_ref: str
    title: str
    url: str
    summary: str = ""
    brand: str = ""
    model: str = ""
    variant: str = ""
    chipset: str = ""
    android_version: str = ""
    build_number: str = ""
    size_bytes: int = 0
    tags: tuple[str, ...] = ()
    topic: str = ""
    gated: bool = False
    has_guide: bool = False
    is_guide: bool = False
    trending: bool = False
    image_url: str = ""


def teaser(text: str, limit: int = TEASER_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _name(obj: Any) -> str:
    return str(getattr(obj, "name", "") or "")


def _recent(moment: Any) -> bool:
    return bool(moment) and moment >= timezone.now() - timedelta(days=TRENDING_DAYS)


def _has_guide(firmware: Any) -> bool:
    if not apps.is_installed("apps.firmwares"):
        return False
    try:
        guides = apps.get_model("firmwares", "GeneratedFlashingGuide")
        lookup: dict[str, Any] = {
            "brand_id": getattr(firmware, "brand_id", None),
            "model_id": getattr(firmware, "model_id", None),
        }
        return guides.objects.filter(**lookup).exists()
    except (LookupError, AttributeError, TypeError, ValueError):
        return False


def firmware_content(
    firmware: Any, firmware_type: str, firmware_id: str
) -> Content | None:
    """The shareable page of an approved firmware (never the download link)."""
    try:
        path = reverse(
            "firmwares:firmware_detail",
            kwargs={"firmware_type": firmware_type, "firmware_id": firmware_id},
        )
    except (KeyError, NoReverseMatch):
        return None
    url = absolute_url(path)
    if firmware is None or not url:
        return None
    brand = _name(getattr(firmware, "brand", None))
    model_obj = getattr(firmware, "model", None)
    model = _name(model_obj)
    variant = _name(getattr(firmware, "variant", None))
    title = " ".join(part for part in (brand, model, variant) if part) or "New firmware"
    chipset = str(
        getattr(firmware, "chipset", "") or getattr(model_obj, "chipset", "") or ""
    )
    chipset_ref = getattr(model_obj, "chipset_ref", None)
    tier = str(getattr(firmware, "access_tier", "free") or "free")
    build = str(getattr(firmware, "build_number", "") or "")
    return Content(
        kind=CONTENT_FIRMWARE,
        object_ref=f"{firmware_type}:{firmware_id}",
        title=title,
        url=url,
        summary=build,
        brand=brand,
        model=model,
        variant=variant,
        chipset=chipset,
        android_version=str(getattr(firmware, "android_version", "") or ""),
        build_number=build,
        size_bytes=int(getattr(firmware, "file_size", 0) or 0),
        tags=tuple(t for t in (brand, model, chipset) if t),
        gated=tier != "free",
        has_guide=_has_guide(firmware),
        trending=_recent(getattr(model_obj, "created_at", None))
        or _recent(getattr(chipset_ref, "created_at", None)),
        image_url=absolute_url(str(getattr(model_obj, "image_url", "") or "")),
    )


def post_content(post: Any) -> Content | None:
    """A published blog post; gated posts share only a short teaser."""
    url = absolute_url(post.get_absolute_url())
    if not url:
        return None
    gated = bool(getattr(post, "is_gated", False))
    summary = (post.summary or "")[:300]
    tags: tuple[str, ...] = ()
    try:
        tags = tuple(str(t.name) for t in post.tags.all()[:6])
    except (AttributeError, ValueError):
        tags = ()
    category = _name(getattr(post, "category", None))
    guide = False
    try:
        guide = bool(getattr(post, "flashing_guide_source", None))
    except (AttributeError, LookupError):
        guide = False
    return Content(
        kind=CONTENT_BLOG,
        object_ref=f"post:{post.pk}",
        title=post.title,
        url=url,
        summary=teaser(summary) if gated else summary,
        tags=tags,
        topic=(tags[0] if tags else category),
        gated=gated,
        is_guide=guide,
        image_url=absolute_url(str(getattr(post, "cover_image_url", "") or "")),
    )


def load(kind: str, object_ref: str) -> Content | None:
    """Rebuild the content from the database (evergreen re-shares, media jobs).

    Returns ``None`` when it is no longer public.
    """
    try:
        if kind == CONTENT_FIRMWARE:
            firmware_type, _sep, firmware_id = object_ref.partition(":")
            model = apps.get_model("firmwares", f"{firmware_type}firmware")
            firmware = (
                model.objects.select_related("brand", "model", "variant")
                .filter(pk=firmware_id, is_verified=True, is_active=True)
                .first()
            )
            if firmware is None:
                return None
            return firmware_content(firmware, firmware_type, firmware_id)
        if kind == CONTENT_BLOG:
            _prefix, _sep, pk = object_ref.partition(":")
            post_model = apps.get_model("blog", "Post")
            post = post_model.published().filter(pk=pk).first()
            return post_content(post) if post is not None else None
    except (LookupError, ValueError, TypeError, ValidationError):
        logger.warning("Cannot reload %s %s for sharing", kind, object_ref)
    return None
