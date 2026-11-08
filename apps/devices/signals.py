from __future__ import annotations

import logging

from django.contrib.auth.signals import user_logged_in
from django.db import DatabaseError
from django.dispatch import receiver

from apps.core.models import AppRegistry
from apps.devices.services import DevicePolicyError, resolve_or_create_device

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid="devices_register_on_login")
def register_device_on_login(request, user, **kwargs) -> None:
    """
    Ensure a device record is registered whenever a user logs in,
    even if AppService routing is skipped elsewhere.
    """
    if request is None or not AppRegistry.get_solo().device_identity_enabled:
        return
    try:
        device, _is_new, context = resolve_or_create_device(
            request, user, service_name="login"
        )
    except DevicePolicyError as exc:
        logger.warning(
            "Device registration rejected on login for user %s: %s", user.pk, exc.reason
        )
        return
    except DatabaseError:
        logger.exception("Device registration failed on successful login")
        return
    if (
        device
        and not device.is_trusted
        and not device.is_blocked
        and device.revoked_at is None
    ):
        request.session["device_registration_prompt"] = {
            "device_name": device.display_name,
            "os_family": device.os_family,
            "browser_family": device.browser_family,
            "active_devices": context.get("current_device_count", 0),
            "max_devices": context.get("max_devices", 3),
            "can_approve": context.get("current_device_count", 0)
            < context.get("max_devices", 3),
        }
