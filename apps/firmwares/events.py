"""Firmware domain events published on the core event bus after a commit.

``firmware.approved`` payload (stable contract for bounty, distribution and others)::

    {
        "firmware": <typed firmware instance>,
        "firmware_id": str, "firmware_type": "official" | ...,
        "pending": <PendingFirmware>,
        "pending_id": str,
        "uploader": <user or None>,
        "reviewer": <user>,
        "brand_id": int | None, "model_id": int | None, "variant_id": int | None,
        "access_tier": "free" | "paid" | "featured",
        "fulfilled_request_ids": [int, ...],
    }

``contribution.approved`` (``{"user_id": <uploader id>, "kind": "firmware"}``) is
published right after it for the referral rewards.

Rewards are granted through ``apps.wallet.api.grant_reward`` (see
``reward_events``) when the wallet app is installed.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.core.events.bus import event_bus

from .models import BaseFirmware, PendingFirmware

logger = logging.getLogger(__name__)

FIRMWARE_APPROVED = "firmware.approved"
CONTRIBUTION_APPROVED = "contribution.approved"


def approval_payload(
    pending: PendingFirmware,
    firmware: BaseFirmware,
    reviewer: Any,
    fulfilled_request_ids: list[int],
) -> dict[str, Any]:
    return {
        "firmware": firmware,
        "firmware_id": str(firmware.pk),
        "firmware_type": pending.routed_category,
        "pending": pending,
        "pending_id": str(pending.pk),
        "uploader": pending.uploader,
        "reviewer": reviewer,
        "brand_id": firmware.brand_id,
        "model_id": firmware.model_id,
        "variant_id": firmware.variant_id,
        "access_tier": firmware.access_tier,
        "fulfilled_request_ids": list(fulfilled_request_ids),
    }


def announce_approval(
    pending: PendingFirmware,
    firmware: BaseFirmware,
    reviewer: Any,
    fulfilled_request_ids: list[int],
) -> None:
    """Publish ``firmware.approved`` and grant the contributor rewards.

    Runs after the approval commits; a failing subscriber or wallet is logged and
    never undoes the approval (the reward keys make a replay idempotent).
    """
    from . import reward_events

    event_bus.publish(
        FIRMWARE_APPROVED,
        approval_payload(pending, firmware, reviewer, fulfilled_request_ids),
    )
    if pending.uploader_id is not None:
        event_bus.publish(
            CONTRIBUTION_APPROVED,
            {"user_id": pending.uploader_id, "kind": "firmware"},
        )
    if not reward_events.wallet_installed():
        return
    reward_events.grant_approval_reward(pending, firmware, reviewer)
    for request_id in fulfilled_request_ids:
        reward_events.grant_request_fulfilled_reward(request_id, firmware, reviewer)
