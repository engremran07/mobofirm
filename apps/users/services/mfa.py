from __future__ import annotations

from django.conf import settings
from django.db import transaction

from apps.users.models import MFADevice, UsersSettings


def mfa_required_globally() -> bool:
    """True when MFA is enforced by the environment flag or the users singleton."""
    return bool(
        getattr(settings, "REQUIRE_MFA", False) or UsersSettings.get_solo().require_mfa
    )


def is_privileged(user) -> bool:
    """Staff and superusers hold the admin plane, so MFA is never optional for them."""
    return bool(
        getattr(settings, "STAFF_MFA_REQUIRED", True)
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )


def mfa_required_for_user(user) -> bool:
    """MFA is mandatory for privileged accounts, and for everyone when enforced globally."""
    return is_privileged(user) or mfa_required_globally()


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
