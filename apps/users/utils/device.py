"""
apps.users.utils.device
-----------------------

Enterprise-grade device fingerprint and limit enforcement utilities for the platform.

Features:
- Registers or updates DeviceFingerprint on each login.
- Enforces per-user device limits (strict / lenient) with ADMIN BYPASS.
- Thread-safe atomic updates, ORM-optimized (Django 5.x).
- Compatible with async-safe authentication and signals.
- Includes periodic admin cleanup utilities.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.devices.models import DeviceFingerprint
from apps.site_settings.models import SiteSettings
from apps.users.models import CustomUser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Device registration / update (atomic, locked)
# ---------------------------------------------------------------------
def register_fingerprint(
    user: CustomUser,
    fingerprint_hash: str,
    os_info: str = "",
    motherboard_id: str = "",
    browser_info: str = "",
) -> DeviceFingerprint | None:
    """
    Create or update a DeviceFingerprint record for the given user.

    Admin users: fingerprint registered but device limits are not enforced.

    Returns:
        DeviceFingerprint | None
    """
    if not user or not fingerprint_hash:
        logger.warning("register_fingerprint: missing user or fingerprint_hash")
        return None

    # Truncate inputs to sane lengths to avoid DB bloat
    os_info = (os_info or "")[:100]
    motherboard_id = (motherboard_id or "")[:100]
    browser_info = (browser_info or "")[:255]

    try:
        with transaction.atomic():
            # Try to lock an existing fingerprint row for update
            qs = DeviceFingerprint.objects.select_for_update().filter(
                user=user, fingerprint_hash=fingerprint_hash
            )
            device: Any = qs.first()
            created = False

            now = timezone.now()

            if device is None:
                # Create new device row
                device = DeviceFingerprint.objects.create(
                    user=user,
                    fingerprint_hash=fingerprint_hash,
                    os_info=os_info,
                    motherboard_id=motherboard_id,
                    browser_info=browser_info,
                    registered_at=now,
                    last_used_at=now,
                    is_active=True,
                )
                created = True
                update_fields: list[str] = []  # nothing to update after create
            else:
                # Update last_used_at and any changed metadata
                update_fields = ["last_used_at"]
                device.last_used_at = now

                if os_info and device.os_info != os_info:
                    device.os_info = os_info
                    update_fields.append("os_info")
                if motherboard_id and device.motherboard_id != motherboard_id:
                    device.motherboard_id = motherboard_id
                    update_fields.append("motherboard_id")
                if browser_info and device.browser_info != browser_info:
                    device.browser_info = browser_info
                    update_fields.append("browser_info")
                if not device.is_active:
                    device.is_active = True
                    update_fields.append("is_active")

                if update_fields:
                    device.save(update_fields=update_fields)

        logger.debug(
            "DeviceFingerprint[%s] %s for %s (admin=%s) — fields: %s",
            device.pk,
            "created" if created else "updated",
            getattr(user, "email", user.pk),
            getattr(user, "is_staff", False),
            ",".join(update_fields) if update_fields else "(none)",
        )
        return device

    except Exception as exc:
        logger.exception(
            "register_fingerprint failed for user=%s -> %s",
            getattr(user, "pk", None),
            exc,
        )
        return None


# ---------------------------------------------------------------------
# Device limit enforcement (atomic, admin bypass)
# ---------------------------------------------------------------------
def enforce_device_limit(user: CustomUser) -> bool:
    """
    Enforce per-user device limits from SiteSettings.

    ADMIN BYPASS: Staff/superusers have unlimited devices.
    REGULAR USERS: Subject to device limit and eviction mode.

    Modes:
        - strict  -> block new device registration if limit reached.
        - lenient -> deactivate oldest fingerprints to make room.

    Returns:
        True if registration/usage allowed, False if blocked.
    """
    if not user:
        return True

    # Admin bypass
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        logger.debug(
            "Device limit bypassed for admin user: %s", getattr(user, "email", user.pk)
        )
        return True

    try:
        settings_obj = SiteSettings.get_solo()
        limit = int(getattr(settings_obj, "max_devices_per_user", 3))
        mode = str(getattr(settings_obj, "fingerprint_mode", "strict")).lower()
    except Exception as exc:
        logger.warning(
            "SiteSettings unavailable; using defaults for device limits: %s", exc
        )
        limit, mode = 3, "strict"

    # Lock the active device rows for this user to avoid races
    with transaction.atomic():
        active_qs = (
            DeviceFingerprint.objects.select_for_update()
            .filter(user=user, is_active=True)
            .only("id", "last_used_at")
            .order_by("last_used_at")
        )
        count = active_qs.count()

        if count < limit:
            logger.debug(
                "User %s within device limit (%d/%d)",
                getattr(user, "email", user.pk),
                count,
                limit,
            )
            return True

        if mode == "lenient":
            # Evict the oldest devices (bulk update)
            to_remove = count - limit + 1
            oldest_devices = list(active_qs[:to_remove])  # evaluated within transaction
            if not oldest_devices:
                # Unexpected, but fail-safe allow
                logger.warning(
                    "No devices found to evict for user %s despite count exceeded",
                    getattr(user, "email", user.pk),
                )
                return False
            try:
                for d in oldest_devices:
                    d.is_active = False  # type: ignore[attr-defined]
                DeviceFingerprint.objects.bulk_update(oldest_devices, ["is_active"])
                logger.info(
                    "Evicted %d oldest device(s) for user %s (lenient mode)",
                    len(oldest_devices),
                    getattr(user, "email", user.pk),
                )
                return True
            except Exception as exc:
                logger.exception(
                    "Device eviction failed for %s: %s",
                    getattr(user, "email", user.pk),
                    exc,
                )
                return False

        # Strict mode: block
        logger.warning(
            "Device registration BLOCKED for %s — device limit reached (%d/%d, strict mode)",
            getattr(user, "email", user.pk),
            count,
            limit,
        )
        return False


# ---------------------------------------------------------------------
# Combined safe helper (record + enforce)
# ---------------------------------------------------------------------
def record_device_fingerprint(
    request,
    user: CustomUser,
    fingerprint_data: dict[str, Any] | None = None,
) -> DeviceFingerprint | None:
    """
    Unified helper for recording device fingerprints during login.

    Raises:
        ValidationError -> missing fingerprint hash
        PermissionError -> strict mode violation for regular users
    """
    fingerprint_data = fingerprint_data or {}

    fingerprint_hash = (
        fingerprint_data.get("fingerprint_hash")
        or (
            getattr(request, "POST", {}).get("device_fp")
            if hasattr(request, "POST")
            else None
        )
        or (
            getattr(request, "COOKIES", {}).get("device_fp")
            if hasattr(request, "COOKIES")
            else None
        )
        or (
            getattr(request, "META", {}).get("HTTP_USER_AGENT")
            if getattr(request, "META", None)
            else None
        )
    )

    if not fingerprint_hash:
        raise ValidationError("record_device_fingerprint: missing fingerprint_hash")

    os_info = fingerprint_data.get("os_info") or (
        getattr(request, "META", {}).get("HTTP_USER_AGENT", "")[:100]
        if getattr(request, "META", None)
        else ""
    )
    motherboard_id = fingerprint_data.get("motherboard_id") or ""
    browser_info = fingerprint_data.get("browser_info") or (
        getattr(request, "META", {}).get("HTTP_USER_AGENT", "")[:255]
        if getattr(request, "META", None)
        else ""
    )

    # Enforce device limit (admin bypass included)
    allowed = enforce_device_limit(user)
    if not allowed:
        raise PermissionError("Device registration blocked (strict mode limit reached)")

    device = register_fingerprint(
        user=user,
        fingerprint_hash=fingerprint_hash,
        os_info=os_info,
        motherboard_id=motherboard_id,
        browser_info=browser_info,
    )

    user_type = (
        "admin"
        if (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
        else "user"
    )
    logger.debug(
        "record_device_fingerprint: device recorded for %s (%s)",
        getattr(user, "email", user.pk),
        user_type,
    )
    return device


# ---------------------------------------------------------------------
# Admin-specific utilities
# ---------------------------------------------------------------------
def get_admin_device_stats() -> dict[str, dict[str, Any]]:
    """
    Returns device statistics for admin users.
    Useful for dashboards or audits.
    """
    stats: dict[str, dict[str, Any]] = {}
    admins = CustomUser.objects.filter(is_staff=True).only("id", "email")
    for user in admins:
        devices_qs = DeviceFingerprint.objects.filter(user=user, is_active=True).only(
            "fingerprint_hash", "last_used_at", "os_info", "browser_info"
        )
        stats_key = user.email or f"User#{user.pk}"
        stats[stats_key] = {
            "total_devices": devices_qs.count(),
            "devices": list(
                devices_qs.values(
                    "fingerprint_hash", "last_used_at", "os_info", "browser_info"
                )
            ),
        }
    return stats


def cleanup_old_admin_devices(days_old: int = 30) -> int:
    """
    Clean up old admin device fingerprints older than `days_old`.
    Returns total number of deleted objects.
    """
    cutoff_date = timezone.now() - timedelta(days=days_old)
    deleted_total = 0

    admins = CustomUser.objects.filter(is_staff=True).only("id", "email")
    for user in admins:
        old_devices_qs = DeviceFingerprint.objects.filter(
            user=user, last_used_at__lt=cutoff_date
        )
        count = old_devices_qs.count()
        if count:
            old_devices_qs.delete()
            deleted_total += count
            logger.info(
                "Cleaned %d old devices for admin %s",
                count,
                user.email or f"User#{user.pk}",
            )

    logger.info("Total admin devices cleaned: %d", deleted_total)
    return deleted_total
