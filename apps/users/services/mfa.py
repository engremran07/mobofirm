from __future__ import annotations

from django.db import transaction

from apps.users.mfa import TOTPService
from apps.users.models import MFADevice


def has_active_totp(user) -> bool:
    return MFADevice.objects.filter(
        user=user,
        device_type="totp",
        is_active=True,
    ).exists()


def verify_mfa_code(user, raw_code: str) -> bool:
    code = (raw_code or "").strip()
    if not code:
        return False

    with transaction.atomic():
        if len(code) == 6 and code.isascii() and code.isdigit():
            devices = MFADevice.objects.select_for_update().filter(
                user=user,
                device_type="totp",
                is_active=True,
            )
            for device in devices:
                if device.verify_code(code):
                    return True

        if len(code) == 8 and code.isascii():
            backup_devices = MFADevice.objects.select_for_update().filter(
                user=user,
                device_type="backup",
                is_active=True,
            )
            for device in backup_devices:
                if device.verify_backup_code(code):
                    return True

    return False
