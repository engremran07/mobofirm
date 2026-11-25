"""Account-erasure contract for device-owned identity and activity data."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.devices.models import (
    Device,
    DeviceBrowserLink,
    DeviceEvent,
    DeviceResetAllowance,
    DeviceSlotRequest,
    DeviceSlotReset,
)


@dataclass(frozen=True)
class ErasureReport:
    """Counts of device-owned records changed for an erased account."""

    devices_deleted: int = 0
    browser_links_deleted: int = 0
    device_events_detached: int = 0
    slot_requests_scrubbed: int = 0
    slot_resets_scrubbed: int = 0
    reset_allowances_deleted: int = 0


def anonymise_user(user: AbstractBaseUser) -> ErasureReport:
    """Remove device credentials and detach or scrub device-related audit history."""
    now = timezone.now()
    with transaction.atomic():
        devices = list(
            Device.objects.select_for_update()
            .filter(user_id=user.pk)
            .values_list("pk", flat=True)
        )
        browser_links_deleted = DeviceBrowserLink.objects.filter(
            user_id=user.pk
        ).count()
        device_events_detached = DeviceEvent.objects.filter(
            Q(user_id=user.pk) | Q(device_id__in=devices)
        ).update(user=None, device=None, ip_hash="", metadata={})

        devices_deleted = len(devices)
        if devices:
            Device.objects.filter(pk__in=devices).delete()

        requests = DeviceSlotRequest.objects.filter(user_id=user.pk)
        slot_requests_scrubbed = requests.filter(
            status=DeviceSlotRequest.Status.OPEN
        ).update(
            status=DeviceSlotRequest.Status.DECLINED,
            resolved_at=now,
            resolved_by=None,
            message="",
            resolution_note="",
        )
        slot_requests_scrubbed += (
            requests.exclude(status=DeviceSlotRequest.Status.OPEN)
            .filter(
                Q(message__gt="")
                | Q(resolution_note__gt="")
                | Q(resolved_by_id=user.pk)
            )
            .update(message="", resolution_note="", resolved_by=None)
        )

        slot_resets_scrubbed = DeviceSlotReset.objects.filter(
            user_id=user.pk, reason__gt=""
        ).update(reason="")
        slot_resets_scrubbed += DeviceSlotReset.objects.filter(actor_id=user.pk).update(
            actor=None
        )
        reset_allowances_deleted, _ = DeviceResetAllowance.objects.filter(
            user_id=user.pk
        ).delete()

    return ErasureReport(
        devices_deleted=devices_deleted,
        browser_links_deleted=browser_links_deleted,
        device_events_detached=device_events_detached,
        slot_requests_scrubbed=slot_requests_scrubbed,
        slot_resets_scrubbed=slot_resets_scrubbed,
        reset_allowances_deleted=reset_allowances_deleted,
    )
