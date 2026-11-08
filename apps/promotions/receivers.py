"""Wiring: lifecycle on save, event-bus progress, and the plug-ins other apps expose."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save

from apps.core.app_service import app_enabled
from apps.core.events.bus import event_bus

from . import gateways
from .effects import discard_uplifts, reward_modifier, settle_uplifts
from .grants import record_progress
from .lifecycle import reconcile
from .models import Promotion
from .rules import CONTRIBUTION_APPROVED

logger = logging.getLogger(__name__)

WALLET_CREDITED = "wallet.credited"
WALLET_REWARD_REFUSED = "wallet.reward_refused"
FIRMWARE_APPROVED = "firmware.approved"
REWARD_REASON = "reward"


def on_promotion_saved(instance: Promotion, raw: bool = False, **kwargs: Any) -> None:
    """Re-evaluate status and announcement after any save (idempotent)."""
    if not raw:
        reconcile(instance.pk)


def _user(user_id: object) -> Any:
    if not isinstance(user_id, int):
        return None
    return get_user_model().objects.filter(pk=user_id).first()


def on_wallet_credited(data: Any) -> None:
    """A reward was credited: settle boost analytics and count earn progress."""
    if not isinstance(data, dict) or data.get("reason") != REWARD_REASON:
        return
    user_id, event_key, key = (
        data.get("user_id"),
        data.get("event_key"),
        data.get("key"),
    )
    if not isinstance(user_id, int) or not isinstance(event_key, str):
        return
    settle_uplifts(user_id, event_key)
    if not app_enabled("promotions") or not isinstance(key, str):
        return
    user = _user(user_id)
    if user is not None:
        record_progress(user, event_key, key)


def on_reward_refused(data: Any) -> None:
    if isinstance(data, dict):
        user_id, event_key = data.get("user_id"), data.get("event_key")
        if isinstance(user_id, int) and isinstance(event_key, str):
            discard_uplifts(user_id, event_key)


def on_firmware_approved(data: Any) -> None:
    """A reviewer approved a contribution: it counts once, never for self-approval."""
    if not isinstance(data, dict) or not app_enabled("promotions"):
        return
    uploader, reviewer = data.get("uploader"), data.get("reviewer")
    pending_id = data.get("pending_id")
    if uploader is None or not pending_id:
        return
    record_progress(
        uploader,
        CONTRIBUTION_APPROVED,
        f"firmware:{pending_id}",
        reviewer_id=getattr(reviewer, "pk", None),
    )


def on_contribution_approved(data: Any) -> None:
    """Other apps' contributions: counted only when the payload names the object."""
    if not isinstance(data, dict) or not app_enabled("promotions"):
        return
    ref = data.get("ref")
    if not isinstance(ref, str) or not ref:
        return
    user = _user(data.get("user_id"))
    reviewer_id = data.get("reviewer_id")
    if user is not None:
        record_progress(
            user,
            CONTRIBUTION_APPROVED,
            f"{data.get('kind', 'contribution')}:{ref}",
            reviewer_id=reviewer_id if isinstance(reviewer_id, int) else None,
        )


def _register_shop_modifier() -> None:
    if not django_apps.is_installed("apps.shop"):
        return
    from apps.shop.entitlements import register_entitlement_modifier

    from .effects import entitlement_modifier

    register_entitlement_modifier(entitlement_modifier)


def _register_banner_rules() -> None:
    from apps.users.services.announcements import (
        register_announcement_gate,
        register_announcement_view_hook,
    )

    from .banners import banner_gate, banner_view_hook

    register_announcement_gate(banner_gate)
    register_announcement_view_hook(banner_view_hook)


def connect_receivers() -> None:
    post_save.connect(
        on_promotion_saved, sender=Promotion, dispatch_uid="promotions_saved_v1"
    )
    event_bus.subscribe(WALLET_CREDITED, on_wallet_credited)
    event_bus.subscribe(WALLET_REWARD_REFUSED, on_reward_refused)
    event_bus.subscribe(FIRMWARE_APPROVED, on_firmware_approved)
    event_bus.subscribe(CONTRIBUTION_APPROVED, on_contribution_approved)
    gateways.register_reward_modifier(reward_modifier)
    _register_shop_modifier()
    _register_banner_rules()
