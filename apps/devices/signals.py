from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.core.models import AppRegistry
from apps.devices.services import resolve_or_create_device

logger = logging.getLogger(__name__)


def _device_identity_enabled() -> bool:
    try:
        reg = AppRegistry.get_solo()
        return bool(getattr(reg, "device_identity_enabled", True))
    except Exception:
        return True


@receiver(user_logged_in, dispatch_uid="devices_register_on_login")
def register_device_on_login(sender: Any, request, user, **kwargs) -> None:
    """
    Ensure a device record is registered whenever a user logs in,
    even if AppService routing is skipped elsewhere.
    """
    if not _device_identity_enabled():
        return
    try:
        # Best-effort registration; do not block login if it fails.
        resolve_or_create_device(request, user, service_name="login")
    except Exception:
        logger.debug("register_device_on_login failed", exc_info=True)
