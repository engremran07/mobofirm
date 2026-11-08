"""Event subscribers: domain events become XP awards (idempotent by key)."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save

from apps.core.events.bus import event_bus
from apps.gamification import services
from apps.gamification.constants import (
    BLOG_PUBLISHED,
    BOUNTY_FULFILLED,
    COMMENT_APPROVED,
    FIRMWARE_APPROVED,
    REFERRAL_REWARDED,
    REQUEST_FULFILLED,
    XP_EVENTS,
)

logger = logging.getLogger(__name__)
_STATE = {"subscribed": False}

# wallet.credited reward events that also earn XP, and the XP event they map to.
WALLET_EVENT_MAP = {
    "referral.verified": REFERRAL_REWARDED,
    "bounty.fulfilled": BOUNTY_FULFILLED,
}


def _user(user_id: object) -> Any | None:
    if not isinstance(user_id, int):
        return None
    return get_user_model().objects.filter(pk=user_id).first()


def on_firmware_approved(data: dict[str, Any]) -> None:
    """``firmware.approved`` (apps.firmwares.events): the uploader is rewarded."""
    uploader = data.get("uploader")
    if uploader is None:
        return
    firmware_ref = f"{data.get('firmware_type')}:{data.get('firmware_id')}"
    services.award(
        uploader,
        FIRMWARE_APPROVED,
        key=f"firmware.approved:{firmware_ref}",
        reference=firmware_ref,
    )
    for request_id in data.get("fulfilled_request_ids") or ():
        services.award(
            uploader,
            REQUEST_FULFILLED,
            key=f"firmware.request_fulfilled:{request_id}",
            reference=f"request:{request_id}",
        )


def on_blog_published(data: dict[str, Any]) -> None:
    """``blog.post_published`` (apps.blog.rewards): only member-authored posts earn XP."""
    author = data.get("author")
    post_id = data.get("post_id")
    if author is None or post_id is None or not data.get("earns_rewards"):
        return
    services.award(
        author,
        BLOG_PUBLISHED,
        key=f"blog.post_published:{post_id}",
        reference=f"post:{post_id}",
    )


def on_wallet_credited(data: dict[str, Any]) -> None:
    """``wallet.credited``: referral and bounty rewards also count as XP."""
    event = WALLET_EVENT_MAP.get(str(data.get("event_key", "")))
    user = _user(data.get("user_id"))
    if event is None or user is None:
        return
    services.award(
        user, event, key=f"{event}:{data.get('key')}", reference=str(data.get("key"))
    )


def on_award_xp(data: dict[str, Any]) -> None:
    """Generic contract: ``{"user_id", "event_key", "key", "reference"?}``."""
    event_key = str(data.get("event_key", ""))
    key = str(data.get("key", ""))
    user = _user(data.get("user_id"))
    if user is None or not key or event_key not in XP_EVENTS:
        logger.warning("Ignored gamification.award_xp payload: %s", data)
        return
    services.award(user, event_key, key=key, reference=str(data.get("reference", "")))


def on_comment_saved(instance: Any, **kwargs: Any) -> None:
    """An approved, undeleted comment by a signed-in user earns XP once."""
    if (
        instance.status != "approved"
        or getattr(instance, "is_deleted", False)
        or instance.user_id is None
    ):
        return
    services.award(
        instance.user,
        COMMENT_APPROVED,
        key=f"comment.approved:{instance.pk}",
        reference=f"comment:{instance.pk}",
    )


def register_handlers() -> None:
    """Subscribe once per process; the post_save hook is de-duplicated by uid."""
    if not _STATE["subscribed"]:
        event_bus.subscribe(FIRMWARE_APPROVED, on_firmware_approved)
        event_bus.subscribe(BLOG_PUBLISHED, on_blog_published)
        event_bus.subscribe("wallet.credited", on_wallet_credited)
        event_bus.subscribe("gamification.award_xp", on_award_xp)
        _STATE["subscribed"] = True
    if apps.is_installed("apps.comments"):
        post_save.connect(
            on_comment_saved,
            sender=apps.get_model("comments", "Comment"),
            dispatch_uid="gamification.comment_saved",
        )
