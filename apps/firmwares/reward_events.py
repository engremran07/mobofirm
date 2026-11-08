"""Credit rewards earned through firmware contributions.

Wallet autodiscovers this module (``apps/<app>/reward_events.py``) and the events are
registered once at import. Amounts, caps and enablement are admin-editable
``RewardRule`` rows owned by the wallet; the numbers below are only the defaults.

Events:

* ``firmware.approved`` - an uploaded free file was approved and published.
* ``firmware.approved.paid`` / ``firmware.approved.featured`` - the same for paid and
  featured files, which carry larger defaults.
* ``firmware.request_fulfilled`` - an approved upload fulfilled a user's firmware
  request; the uploader earns it once per fulfilled request.
"""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps as django_apps

from apps.wallet.api import RewardEvent, register_reward_event

from .models import AccessTier, BaseFirmware, PendingFirmware

logger = logging.getLogger(__name__)

APPROVED_EVENT = "firmware.approved"
APPROVED_PAID_EVENT = "firmware.approved.paid"
APPROVED_FEATURED_EVENT = "firmware.approved.featured"
REQUEST_FULFILLED_EVENT = "firmware.request_fulfilled"

TIER_EVENTS: dict[str, str] = {
    AccessTier.FREE.value: APPROVED_EVENT,
    AccessTier.PAID.value: APPROVED_PAID_EVENT,
    AccessTier.FEATURED.value: APPROVED_FEATURED_EVENT,
}

register_reward_event(
    RewardEvent(
        key=APPROVED_EVENT,
        label="Free firmware approved",
        app="firmwares",
        default_credits=10,
        default_daily_cap=100,
        default_monthly_cap=1000,
    )
)
register_reward_event(
    RewardEvent(
        key=APPROVED_PAID_EVENT,
        label="Paid firmware approved",
        app="firmwares",
        default_credits=15,
        default_daily_cap=150,
        default_monthly_cap=1500,
    )
)
register_reward_event(
    RewardEvent(
        key=APPROVED_FEATURED_EVENT,
        label="Featured firmware approved",
        app="firmwares",
        default_credits=25,
        default_daily_cap=250,
        default_monthly_cap=2500,
    )
)
register_reward_event(
    RewardEvent(
        key=REQUEST_FULFILLED_EVENT,
        label="Firmware request fulfilled",
        app="firmwares",
        default_credits=20,
        default_daily_cap=100,
        default_monthly_cap=1000,
    )
)


def wallet_installed() -> bool:
    return django_apps.is_installed("apps.wallet")


def _reference(firmware: BaseFirmware) -> str:
    return f"{type(firmware).__name__}:{firmware.pk}"


def grant_approval_reward(
    pending: PendingFirmware, firmware: BaseFirmware, reviewer: Any
) -> Any:
    """Reward the uploader once per approved upload; never their own approval."""
    uploader = pending.uploader
    if uploader is None or reviewer is None or uploader.pk == reviewer.pk:
        return None
    from apps.wallet.api import grant_reward

    return grant_reward(
        uploader,
        TIER_EVENTS.get(firmware.access_tier, APPROVED_EVENT),
        key=f"{APPROVED_EVENT}:{pending.pk}",
        reference=_reference(firmware),
        context={
            "pending_id": str(pending.pk),
            "firmware_id": str(firmware.pk),
            "tier": firmware.access_tier,
        },
    )


def grant_request_fulfilled_reward(
    request_id: int, firmware: BaseFirmware, reviewer: Any
) -> Any:
    """Reward the uploader whose approved file fulfilled a firmware request."""
    uploader = firmware.uploader
    if uploader is None or reviewer is None or uploader.pk == reviewer.pk:
        return None
    from apps.wallet.api import grant_reward

    return grant_reward(
        uploader,
        REQUEST_FULFILLED_EVENT,
        key=f"{REQUEST_FULFILLED_EVENT}:{request_id}",
        reference=_reference(firmware),
        context={"request_id": request_id, "firmware_id": str(firmware.pk)},
    )
