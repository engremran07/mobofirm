"""Event subscribers: new approved firmware and newly published blog posts."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps

from apps.core.events.bus import event_bus
from apps.core.signals import blog_post_published
from apps.distribution import content as content_module
from apps.distribution import services
from apps.distribution.content import Content

logger = logging.getLogger(__name__)
_STATE = {"subscribed": False}


def firmware_content(payload: dict[str, Any]) -> Content | None:
    """The shareable page of an approved firmware (never the download link)."""
    firmware = payload.get("firmware")
    try:
        firmware_type, firmware_id = payload["firmware_type"], payload["firmware_id"]
    except KeyError:
        return None
    if firmware is None or not getattr(firmware, "is_verified", True):
        return None
    return content_module.firmware_content(firmware, firmware_type, firmware_id)


def on_firmware_approved(data: dict[str, Any]) -> None:
    item = firmware_content(data)
    if item is not None:
        services.enqueue(item)


def on_blog_post_published(post: Any, **kwargs: Any) -> None:
    """Share a post once, when it first becomes public (human-authored only)."""
    if getattr(post, "is_ai_generated", False):
        return
    if apps.is_installed("apps.firmwares"):
        link_model = apps.get_model("firmwares", "AutomatedBlogLink")
        if link_model.objects.filter(post=post).exists():
            return  # its firmware is shared on its own
    item = content_module.post_content(post)
    if item is not None:
        services.enqueue(item)


def register_handlers() -> None:
    """Subscribe once per process."""
    if _STATE["subscribed"]:
        return
    event_bus.subscribe("firmware.approved", on_firmware_approved)
    blog_post_published.connect(
        on_blog_post_published, dispatch_uid="distribution.blog_post_published"
    )
    _STATE["subscribed"] = True
