"""Device slot resets: freeing approved-browser slots for an account at its limit.

A *slot reset* releases every approved browser of one account (``is_trusted`` goes
back to false, so each browser shows up as "awaiting approval" and can be approved
again within the limit). Nothing is deleted: the ``Device`` rows, their events and a
permanent :class:`DeviceSlotReset` row remain as the audit trail.

Three methods, all audited and idempotent on a caller-supplied ``key``:

* ``staff``       – staff with ``devices.reset_device_slots`` (directly or by resolving a
                    :class:`DeviceSlotRequest` the user filed with "ask staff");
* ``credits``     – the user pays the ``device.slot_reset`` price through the wallet;
* ``rewarded_ad`` – the user spends a :class:`DeviceResetAllowance` earned by
                    completing a rewarded ad whose reward type is ``device_reset``.

Self-service resets (credits, rewarded ad) share one calendar-month cap
(``DeviceConfig.slot_reset_monthly_cap``); staff resets are not counted. Everything
runs in one transaction under the account row lock, so a failure rolls back the
credit spend, the allowance and the release together and concurrent requests cannot
double-spend.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.services.notifications import notification_service
from apps.devices import credits as slot_credits
from apps.devices.credits import CreditsUnavailable, InsufficientCreditsError
from apps.devices.models import (
    Device,
    DeviceConfig,
    DeviceEvent,
    DeviceResetAllowance,
    DeviceSlotRequest,
    DeviceSlotReset,
)
from apps.devices.services import (
    active_devices_queryset,
    get_active_device_count,
    get_device_limit,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ALLOWANCE_TTL_DAYS",
    "CreditsUnavailable",
    "InsufficientCreditsError",
    "SlotOptions",
    "SlotResetError",
    "grant_reset_allowance",
    "monthly_cap",
    "request_staff_reset",
    "reset_slots_as_staff",
    "reset_slots_with_allowance",
    "reset_slots_with_credits",
    "resolve_slot_request",
    "self_service_resets_used",
    "slot_options",
    "unused_allowances",
]

ALLOWANCE_TTL_DAYS = 30
STAFF_PERMISSION = "devices.reset_device_slots"
SELF_SERVICE_METHODS = (
    DeviceSlotReset.Method.CREDITS,
    DeviceSlotReset.Method.REWARDED_AD,
)
EVENT_TYPE = "slot_reset"
REWARDED_DEVICE_RESET = "device_reset"


class SlotResetError(Exception):
    """A rule refused the reset; ``code`` is stable, ``message`` is user-facing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RewardedOffer:
    """An enabled ``device_reset`` rewarded ad the user may watch."""

    name: str
    url: str
    description: str


@dataclass(frozen=True)
class SlotOptions:
    """Everything the "free a slot" UI needs, resolved in one place."""

    limit: int
    active: int
    request_open: bool
    credits_price: int | None
    balance: int
    earn_url: str
    resets_used: int
    resets_cap: int
    allowances: int
    rewarded: RewardedOffer | None

    @property
    def at_limit(self) -> bool:
        return self.active >= self.limit

    @property
    def cap_reached(self) -> bool:
        return self.resets_used >= self.resets_cap

    @property
    def resets_left(self) -> int:
        return max(0, self.resets_cap - self.resets_used)

    @property
    def can_pay(self) -> bool:
        return self.credits_price is not None and not self.cap_reached

    @property
    def missing_credits(self) -> int:
        if self.credits_price is None:
            return 0
        return max(0, self.credits_price - self.balance)


def monthly_cap() -> int:
    return int(DeviceConfig.get_solo().slot_reset_monthly_cap)


def _month_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def self_service_resets_used(user: Any, *, now: datetime | None = None) -> int:
    """Credit and rewarded-ad resets this account used in the current calendar month."""
    start = _month_start(now or timezone.now())
    return DeviceSlotReset.objects.filter(
        user=user, method__in=SELF_SERVICE_METHODS, created_at__gte=start
    ).count()


def unused_allowances(user: Any, *, now: datetime | None = None) -> int:
    return DeviceResetAllowance.objects.filter(
        user=user, consumed_at__isnull=True, expires_at__gt=now or timezone.now()
    ).count()


def _rewarded_offer() -> RewardedOffer | None:
    if not django_apps.is_installed("apps.ads"):
        return None
    from apps.ads.api import rewarded_offer

    offer = rewarded_offer(REWARDED_DEVICE_RESET)
    if not offer.get("enabled"):
        return None
    return RewardedOffer(
        name=str(offer["name"]),
        url=str(offer["url"]),
        description=str(offer.get("reward_description", "")),
    )


def slot_options(user: Any) -> SlotOptions:
    """The ways ``user`` can free a slot right now."""
    return SlotOptions(
        limit=get_device_limit(user),
        active=get_active_device_count(user),
        request_open=DeviceSlotRequest.objects.filter(
            user=user, status=DeviceSlotRequest.Status.OPEN
        ).exists(),
        credits_price=slot_credits.price(user),
        balance=slot_credits.balance(user),
        earn_url=slot_credits.earn_url(),
        resets_used=self_service_resets_used(user),
        resets_cap=monthly_cap(),
        allowances=unused_allowances(user),
        rewarded=_rewarded_offer(),
    )


# --- core -------------------------------------------------------------------


def _lock_user(user: Any) -> Any:
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def _replay(user: Any, key: str) -> DeviceSlotReset | None:
    """The reset already recorded under ``key``; another account's key is refused."""
    existing = DeviceSlotReset.objects.filter(key=key).first()
    if existing is None:
        return None
    if existing.user_id != user.pk:
        raise SlotResetError("key_conflict", "This request key is already in use.")
    return existing


def _require_room_in_cap(user: Any) -> None:
    cap = monthly_cap()
    if cap <= 0:
        raise SlotResetError(
            "resets_disabled", "Self-service slot resets are switched off."
        )
    if self_service_resets_used(user) >= cap:
        raise SlotResetError(
            "monthly_cap",
            f"You have used all {cap} self-service resets for this month.",
        )


def _occupied(user: Any) -> list[Device]:
    devices = list(active_devices_queryset(user).select_for_update())
    if not devices:
        raise SlotResetError(
            "nothing_to_reset", "No approved browsers are using a slot right now."
        )
    return devices


def _release(
    user: Any,
    devices: list[Device],
    *,
    method: str,
    key: str,
    actor: Any = None,
    reason: str = "",
    credits_spent: int = 0,
    ledger_key: str = "",
    allowance_key: str = "",
) -> DeviceSlotReset:
    """Release ``devices`` and write the audit rows; the caller holds the lock."""
    ids = [device.pk for device in devices]
    Device.objects.filter(pk__in=ids).update(is_trusted=False)
    reset = DeviceSlotReset.objects.create(
        user=user,
        method=method,
        key=key,
        devices_released=len(ids),
        credits_spent=credits_spent,
        ledger_key=ledger_key,
        allowance_key=allowance_key,
        actor=actor,
        reason=reason[:255],
    )
    metadata = {
        "method": method,
        "reset_id": reset.pk,
        "key": key,
        "actor_id": getattr(actor, "pk", None),
        "credits_spent": credits_spent,
    }
    summary_reason = f"{EVENT_TYPE}:{method}"
    DeviceEvent.objects.bulk_create(
        [
            DeviceEvent(
                device=None,
                user=user,
                event_type=EVENT_TYPE,
                success=True,
                reason=summary_reason,
                metadata={**metadata, "released_device_ids": [str(pk) for pk in ids]},
            ),
            *(
                DeviceEvent(
                    device=device,
                    user=user,
                    event_type=EVENT_TYPE,
                    success=True,
                    reason=summary_reason,
                    metadata=metadata,
                )
                for device in devices
            ),
        ]
    )
    _notify_user(
        user.pk,
        "Browser slots reset",
        f"{len(ids)} approved browser(s) were released ({reset.get_method_display()})."
        " Approve the browsers you use again from Registered browsers.",
        reverse("devices:my_devices"),
    )
    return reset


def _notify_user(user_id: int, title: str, message: str, url: str) -> None:
    transaction.on_commit(
        lambda: notification_service.send(
            user_id, title, message, "info", url=url, action_type="system"
        )
    )


# --- public reset methods ----------------------------------------------------


def reset_slots_as_staff(
    user: Any, *, actor: Any, reason: str, key: str | None = None
) -> DeviceSlotReset:
    """Release ``user``'s approved browsers on behalf of staff (not counted in the cap)."""
    if not actor.has_perm(STAFF_PERMISSION):
        raise PermissionDenied("You may not reset device slots.")
    reason_text = reason.strip()
    if not reason_text:
        raise SlotResetError("reason_required", "A reason is required.")
    reset_key = key or f"staff:{uuid.uuid4().hex}"
    with transaction.atomic():
        locked = _lock_user(user)
        replay = _replay(locked, reset_key)
        if replay is not None:
            return replay
        devices = _occupied(locked)
        return _release(
            locked,
            devices,
            method=DeviceSlotReset.Method.STAFF,
            key=reset_key,
            actor=actor,
            reason=reason_text,
        )


def reset_slots_with_credits(user: Any, *, key: str) -> DeviceSlotReset:
    """Pay the ``device.slot_reset`` price and release the approved browsers."""
    reset_key = f"credits:{key}"
    with transaction.atomic():
        locked = _lock_user(user)
        replay = _replay(locked, reset_key)
        if replay is not None:
            return replay
        _require_room_in_cap(locked)
        devices = _occupied(locked)
        ledger_key = f"device.slot_reset:{locked.pk}:{key}"
        spent = slot_credits.spend(
            locked, key=ledger_key, reference=f"devices:slot_reset:{key}"
        )
        return _release(
            locked,
            devices,
            method=DeviceSlotReset.Method.CREDITS,
            key=reset_key,
            reason="Paid with credits",
            credits_spent=spent,
            ledger_key=ledger_key,
        )


def reset_slots_with_allowance(user: Any, *, key: str) -> DeviceSlotReset:
    """Spend one earned (rewarded-ad) allowance and release the approved browsers."""
    reset_key = f"allowance:{key}"
    with transaction.atomic():
        locked = _lock_user(user)
        replay = _replay(locked, reset_key)
        if replay is not None:
            return replay
        _require_room_in_cap(locked)
        devices = _occupied(locked)
        now = timezone.now()
        allowance = (
            DeviceResetAllowance.objects.select_for_update()
            .filter(user=locked, consumed_at__isnull=True, expires_at__gt=now)
            .order_by("expires_at", "pk")
            .first()
        )
        if allowance is None:
            raise SlotResetError(
                "no_allowance", "You have no unused reset from a rewarded ad."
            )
        allowance.consumed_at = now
        allowance.save(update_fields=["consumed_at"])
        return _release(
            locked,
            devices,
            method=DeviceSlotReset.Method.REWARDED_AD,
            key=reset_key,
            reason="Rewarded ad",
            allowance_key=allowance.key,
        )


def grant_reset_allowance(
    user: Any, *, key: str, source: str, ttl_days: int = ALLOWANCE_TTL_DAYS
) -> DeviceResetAllowance:
    """Record one earned reset; idempotent on ``key`` (a replayed event grants nothing)."""
    with transaction.atomic():
        existing = DeviceResetAllowance.objects.filter(key=key).first()
        if existing is not None:
            if existing.user_id != user.pk:
                raise SlotResetError("key_conflict", "This key is already in use.")
            return existing
        return DeviceResetAllowance.objects.create(
            user=user,
            key=key,
            source=source[:32],
            expires_at=timezone.now() + timedelta(days=ttl_days),
        )


# --- ask staff ----------------------------------------------------------------


def _staff_recipients() -> list[Any]:
    perm = Q(user_permissions__codename="reset_device_slots") | Q(
        groups__permissions__codename="reset_device_slots"
    )
    return list(
        get_user_model()
        .objects.filter(is_active=True, is_staff=True)
        .filter(Q(is_superuser=True) | perm)
        .distinct()[:50]
    )


def request_staff_reset(user: Any, message: str = "") -> tuple[DeviceSlotRequest, bool]:
    """File (or return the open) "ask staff" request; staff are notified once."""
    with transaction.atomic():
        locked = _lock_user(user)
        existing = DeviceSlotRequest.objects.filter(
            user=locked, status=DeviceSlotRequest.Status.OPEN
        ).first()
        if existing is not None:
            return existing, False
        request_row = DeviceSlotRequest.objects.create(
            user=locked, message=message.strip()[:500]
        )
        url = reverse("admin:devices_deviceslotrequest_changelist")
        for staff in _staff_recipients():
            _notify_user(
                staff.pk,
                "Device slot reset requested",
                f"{locked} reached their device limit and asked for a reset.",
                url,
            )
    return request_row, True


def resolve_slot_request(
    slot_request: DeviceSlotRequest, *, actor: Any, reset: bool, note: str
) -> DeviceSlotRequest:
    """Staff answer an open request: reset the account's slots or decline with a note."""
    if not actor.has_perm(STAFF_PERMISSION):
        raise PermissionDenied("You may not resolve slot requests.")
    note_text = note.strip()
    if not note_text:
        raise SlotResetError("reason_required", "A note is required.")
    with transaction.atomic():
        row = DeviceSlotRequest.objects.select_for_update().get(pk=slot_request.pk)
        if row.status != DeviceSlotRequest.Status.OPEN:
            raise SlotResetError("already_resolved", "This request was already closed.")
        if reset:
            row.reset = reset_slots_as_staff(
                row.user,
                actor=actor,
                reason=note_text,
                key=f"staff-request:{row.pk}",
            )
            row.status = DeviceSlotRequest.Status.RESOLVED
        else:
            row.status = DeviceSlotRequest.Status.DECLINED
        row.resolved_by = actor
        row.resolved_at = timezone.now()
        row.resolution_note = note_text[:255]
        row.save()
        if not reset:
            _notify_user(
                row.user_id,
                "Slot reset request declined",
                note_text,
                reverse("devices:my_devices"),
            )
    return row
