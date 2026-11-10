from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from apps.core.utils.ip import get_client_ip

if TYPE_CHECKING:
    from apps.devices.models import BehaviorInsight
from apps.devices.models import (
    AppPolicy,
    Device,
    DeviceBrowserLink,
    DeviceConfig,
    DeviceEvent,
)

logger = logging.getLogger(__name__)

Policy = dict[str, Any]
Identity = dict[str, Any]
DEVICE_COOKIE_NAME = "mobofirm_device"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
UNLIMITED_DEVICES = 999999
_BROWSER_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,128}$")


class DevicePolicyError(Exception):
    def __init__(self, reason: str, context: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


def ensure_browser_key(request) -> str:
    """Resolve or create the random first-party browser-profile key for this request."""
    key = getattr(request, "device_browser_key", None) or request.COOKIES.get(
        DEVICE_COOKIE_NAME, ""
    )
    if not isinstance(key, str) or not _BROWSER_KEY_PATTERN.fullmatch(key):
        key = secrets.token_urlsafe(32)
        request.device_browser_key_needs_cookie = True
    request.device_browser_key = key
    return key


def hash_browser_key(key: str) -> str:
    """Return a keyed digest; the browser's bearer key is never stored in the database."""
    return salted_hmac(
        "apps.devices.browser-profile.v1",
        key,
        algorithm="sha256",
    ).hexdigest()


def active_devices_queryset(user):
    """The registrations that occupy a slot: approved, identified, unblocked, not revoked.

    One operating system (platform credential) is one slot however many browsers run
    on it; a browser-only fallback registration is one slot per browser profile.
    """
    return Device.objects.filter(
        Q(browser_key_hash__gt="") | Q(os_credential_id_hash__gt=""),
        user=user,
        is_trusted=True,
        is_blocked=False,
        revoked_at__isnull=True,
    )


def get_active_device_count(user) -> int:
    return active_devices_queryset(user).count()


def user_has_os_registration(user) -> bool:
    """True when the account has a usable registered operating system."""
    return Device.objects.filter(
        user=user, os_credential_id_hash__gt="", revoked_at__isnull=True
    ).exists()


def os_link_max_age_days() -> int:
    """How long a browser stays recognised on its OS before it re-proves the credential.

    A browser profile restored onto a reinstalled OS keeps its cookie, so the link
    cannot be permanent; the credential, which a reinstall destroys, is re-checked.
    """
    return max(1, int(getattr(settings, "DEVICE_OS_LINK_MAX_AGE_DAYS", 30)))


def find_current_device(request, user, *, for_update: bool = False) -> Device | None:
    """The registration the requesting browser maps to, without creating anything.

    A browser that proved possession of an OS credential resolves to that OS's
    ``Device`` through its ``DeviceBrowserLink``; otherwise the browser's own
    cookie-keyed (fallback) registration is used.
    """
    key_hash = hash_browser_key(ensure_browser_key(request))
    devices = Device.objects.select_for_update() if for_update else Device.objects
    linked_id = (
        DeviceBrowserLink.objects.filter(
            user=user,
            browser_key_hash=key_hash,
            verified_at__gte=timezone.now() - timedelta(days=os_link_max_age_days()),
        )
        .values_list("device_id", flat=True)
        .first()
    )
    if linked_id is not None:
        return devices.filter(pk=linked_id, user=user).first()
    return devices.filter(user=user, browser_key_hash=key_hash).first()


def _entitled_device_limit(user) -> int | None:
    """The account's device allowance from the one entitlement resolver.

    ``None`` means unlimited. Without the shop app there is no package system, so
    ``DEVICE_FALLBACK_LIMIT`` (default 3) is the single allowance for everyone.
    """
    if not django_apps.is_installed("apps.shop"):
        return int(getattr(settings, "DEVICE_FALLBACK_LIMIT", 3))
    from apps.shop.entitlements import effective_entitlements

    return effective_entitlements(user).device_limit


def get_device_limit(user) -> int:
    """Approved browsers allowed for ``user``: package + staff override + campaigns."""
    if getattr(user, "is_superuser", False):
        return UNLIMITED_DEVICES
    limit = _entitled_device_limit(user)
    return UNLIMITED_DEVICES if limit is None else int(limit)


def get_device_registration_summary(user) -> dict[str, int]:
    maximum = get_device_limit(user)
    active = get_active_device_count(user)
    return {
        "active_devices": active,
        "max_devices": maximum,
        "remaining_devices": max(0, maximum - active),
        "total_devices": Device.objects.filter(user=user).count(),
    }


def _parse_browser_family(ua: str) -> str:
    ua_l = ua.lower()
    if "chrome" in ua_l and "edg" not in ua_l and "opr" not in ua_l:
        return "Chrome"
    if "edg" in ua_l:
        return "Edge"
    if "firefox" in ua_l:
        return "Firefox"
    if "safari" in ua_l and "chrome" not in ua_l:
        return "Safari"
    if "opr" in ua_l or "opera" in ua_l:
        return "Opera"
    if "msie" in ua_l or "trident" in ua_l:
        return "IE"
    return "Browser"


def _parse_os_family(ua: str) -> str:
    ua_l = ua.lower()
    if "windows" in ua_l:
        return "Windows"
    if "mac os" in ua_l or "macintosh" in ua_l:
        return "macOS"
    if "android" in ua_l:
        return "Android"
    if "iphone" in ua_l or "ipad" in ua_l or "ios" in ua_l:
        return "iOS"
    if "linux" in ua_l:
        return "Linux"
    return "OS"


def describe_platform(ua: str) -> tuple[str, str]:
    """(operating-system family, device type) read from a User-Agent; a label only."""
    os_family = _parse_os_family(ua)
    ua_l = ua.lower()
    if "ipad" in ua_l or ("android" in ua_l and "mobile" not in ua_l):
        return os_family, "tablet"
    if any(term in ua_l for term in ("mobile", "iphone", "android")):
        return os_family, "phone"
    return os_family, "computer"


def mark_device_trusted(device_id, user_id) -> bool:
    """Trust a registered browser profile, honouring blocks and the device limit."""
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=user_id)
        device = (
            Device.objects.select_for_update().filter(id=device_id, user=user).first()
        )
        if device is None or device.revoked_at is not None:
            return False
        if device.is_trusted:
            return True
        if get_active_device_count(user) >= get_device_limit(user):
            return False
        device.is_trusted = True
        device.save(update_fields=["is_trusted"])
        DeviceEvent.objects.create(
            device=device,
            user=user,
            event_type="device_trusted",
            success=True,
            reason="trusted_by_account_flow",
        )
    return True


def attach_device_cookie(
    response: HttpResponse,
    max_age_days: int = 365,
    request=None,
) -> HttpResponse:
    key = getattr(request, "device_browser_key", None) if request else None
    if response and key and getattr(request, "device_browser_key_needs_cookie", False):
        response.set_cookie(
            DEVICE_COOKIE_NAME,
            key,
            max_age=max_age_days * 24 * 60 * 60,
            httponly=True,
            secure=bool(request.is_secure() or settings.SESSION_COOKIE_SECURE),
            samesite="Lax",
            path="/",
        )
    return response


def get_effective_policy(request, user=None, service_name: str | None = None) -> Policy:
    """
    Combine global DeviceConfig + AppPolicy into an effective policy snapshot.
    """
    config = DeviceConfig.get_solo()
    app_name = getattr(settings, "DEVICE_APP_NAME", "default")
    try:
        app_policy = AppPolicy.objects.get(name=app_name)
    except AppPolicy.DoesNotExist:
        app_policy = None

    policy: Policy = {
        "basic_fp": bool(getattr(config, "basic_fingerprinting_enabled", True)),
        "enhanced_fp": bool(getattr(config, "enhanced_fingerprinting_enabled", False)),
        "device_locking_mode": "none",
        "mfa_requirement": "none",
        "ai_risk_scoring": bool(getattr(config, "ai_risk_scoring_enabled", False)),
        "max_devices": get_device_limit(user),
        "monthly_quota": getattr(config, "monthly_device_quota", None),
        "yearly_quota": getattr(config, "yearly_device_quota", None),
        "strict_new_device_login": bool(
            getattr(config, "strict_new_device_login", False)
        ),
        "require_mfa_on_new_device": bool(
            getattr(config, "require_mfa_on_new_device", False)
        ),
        "allow_server_fallback": bool(getattr(config, "allow_server_fallback", True)),
        "device_expiry_days": getattr(config, "device_expiry_days", None),
        "risk_mfa_threshold": int(getattr(config, "risk_mfa_threshold", 75) or 75),
        "service_rules": {},
    }

    if app_policy:
        policy.update(
            {
                "basic_fp": policy["basic_fp"] and app_policy.basic_fingerprinting,
                "enhanced_fp": policy["enhanced_fp"]
                and app_policy.enhanced_fingerprinting,
                "device_locking_mode": app_policy.device_locking_mode
                or policy["device_locking_mode"],
                "mfa_requirement": app_policy.mfa_requirement
                or policy["mfa_requirement"],
                "ai_risk_scoring": policy["ai_risk_scoring"]
                or app_policy.ai_risk_scoring,
                "service_rules": app_policy.service_level_rules or {},
                "monthly_quota": app_policy.monthly_device_quota
                or policy["monthly_quota"],
                "yearly_quota": app_policy.yearly_device_quota
                or policy["yearly_quota"],
            }
        )

    # -----------------------------------------------------------------
    # SUPER ADMIN OVERRIDE (Unlimited Access)
    # -----------------------------------------------------------------
    # Superusers get unlimited devices to avoid lockout scenarios.
    # Regular staff/users are subject to normal policy limits.
    if user and user.is_authenticated and user.is_superuser:
        policy["max_devices"] = UNLIMITED_DEVICES
        policy["monthly_quota"] = None
        policy["yearly_quota"] = None
        policy["device_locking_mode"] = "none"
    else:
        # The app policy's locking/MFA modes are enforced like the global flags.
        policy["strict_new_device_login"] = (
            policy["strict_new_device_login"]
            or policy["device_locking_mode"] == "strict"
        )
        policy["require_mfa_on_new_device"] = policy[
            "require_mfa_on_new_device"
        ] or policy["mfa_requirement"] in {"new_device", "always"}

    if service_name:
        svc_rules = policy["service_rules"].get(service_name, {})
        if isinstance(svc_rules, dict):
            policy["service_rules"] = svc_rules
        else:
            policy["service_rules"] = {}
    return policy


def resolve_identity(
    request,
    user=None,
    service_name: str | None = None,
) -> Identity:
    """Resolve a browser profile without deriving identity from machine traits."""
    policy = get_effective_policy(request, user=user, service_name=service_name)
    ip = get_client_ip(request) or ""
    country = (
        request.META.get("HTTP_CF_IPCOUNTRY")
        or request.META.get("GEOIP_COUNTRY_CODE")
        or request.META.get("HTTP_X_COUNTRY_CODE")
        or ""
    ).strip()
    ua = (request.META.get("HTTP_USER_AGENT") or "").strip()
    key = ensure_browser_key(request)
    return {
        "browser_key_hash": hash_browser_key(key),
        "identity_level": "primary",
        "policy_snapshot": policy,
        "ip": ip,
        "country": country,
        "user_agent": ua,
    }


def enforce_registration_limits(user, policy: Policy, now) -> None:
    """Reject a new browser registration that would exceed configured quotas."""
    registrations = Device.objects.filter(user=user)
    pending_cap = int(getattr(settings, "DEVICE_MAX_PENDING_PER_USER", 10))
    if pending_cap and not getattr(user, "is_superuser", False):
        pending = registrations.filter(is_trusted=False, revoked_at__isnull=True)
        if pending.count() >= pending_cap:
            raise DevicePolicyError("limit_reached", {"max_pending": pending_cap})

    monthly = policy.get("monthly_quota")
    if monthly and registrations.filter(
        first_seen_at__gte=now - timedelta(days=30)
    ).count() >= int(monthly):
        raise DevicePolicyError("monthly_device_quota", {"limit": int(monthly)})
    yearly = policy.get("yearly_quota")
    if yearly and registrations.filter(
        first_seen_at__gte=now - timedelta(days=365)
    ).count() >= int(yearly):
        raise DevicePolicyError("yearly_device_quota", {"limit": int(yearly)})


def resolve_or_create_device(
    request,
    user,
    service_name: str | None = None,
) -> tuple[Device | None, bool, dict[str, Any]]:
    """
    Resolve or create the user's browser-profile registration.

    New registrations remain pending until the user approves them. Capacity is
    checked at approval time so a full account can still sign in and reach its
    device-management/support options.
    Returns (device, is_new, context).
    """
    ident = resolve_identity(request, user=user, service_name=service_name)
    now = timezone.now()
    ua = ident.get("user_agent", "") or ""
    ip = ident.get("ip", "") or ""
    key_hash = ident["browser_key_hash"]
    browser_family = _parse_browser_family(ua)
    os_family, device_type = describe_platform(ua)
    display_name = f"{os_family} {device_type} · {browser_family}"

    prev_event_ip = None
    prev_country = None
    last_evt = (
        DeviceEvent.objects.filter(user=user)
        .order_by("-created_at")
        .only("ip", "geo_region")
        .first()
    )
    if last_evt is not None:
        prev_event_ip = last_evt.ip
        prev_country = last_evt.geo_region

    device = find_current_device(request, user)
    is_new = device is None

    if device is None:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=user.pk)
            device = find_current_device(request, user, for_update=True)
            if device is None:
                enforce_registration_limits(user, ident["policy_snapshot"], now)
                device = Device.objects.create(
                    user=user,
                    browser_key_hash=key_hash,
                    display_name=display_name,
                    os_name=os_family,
                    os_family=os_family,
                    browser_family=browser_family,
                    metadata={"device_type": device_type},
                    first_seen_at=now,
                    last_seen_at=now,
                    is_trusted=False,
                )
                is_new = True
            else:
                is_new = False

    expiry_days = ident["policy_snapshot"].get("device_expiry_days")
    if (
        expiry_days
        and device.is_trusted
        and device.revoked_at is None
        and device.last_seen_at < now - timedelta(days=int(expiry_days))
    ):
        device.is_trusted = False
        device.save(update_fields=["is_trusted"])
        DeviceEvent.objects.create(
            device=device,
            user=user,
            event_type="policy_violation",
            success=False,
            reason="trust_expired",
        )

    if device.revoked_at is None and device.is_os_identity:
        # The label of an OS registration is fixed at registration time; the browser
        # making this request is irrelevant to it.
        device.last_seen_at = now
        device.save(update_fields=["last_seen_at"])
    elif device.revoked_at is None:
        device.last_seen_at = now
        device.display_name = display_name
        device.os_name = os_family
        device.os_family = os_family
        device.browser_family = browser_family
        device.metadata = {"device_type": device_type}
        device.save(
            update_fields=[
                "last_seen_at",
                "display_name",
                "os_name",
                "os_family",
                "browser_family",
                "metadata",
            ]
        )

    # IP and country changes are risk signals only, never identity inputs. The
    # score is recomputed for each sign-in so it cannot ratchet upwards forever.
    country = ident.get("country", "") or ""
    risk = 40 if is_new else 0
    if prev_event_ip and ip and prev_event_ip != ip:
        risk += 15
    if prev_country and country and prev_country != country:
        risk += 15
    risk = min(100, risk)
    if device.risk_score != risk:
        device.risk_score = risk
        device.save(update_fields=["risk_score"])
    ident["risk_score"] = risk
    ident["is_new"] = is_new
    # A new, unrecognised browser of an account that already registered an OS may
    # simply be another browser on that computer: ask for the OS check, not a
    # "new device" alert.
    ident["os_check_pending"] = bool(is_new and user_has_os_registration(user))
    active_count = get_active_device_count(user)
    maximum = get_device_limit(user)
    ident["remaining_devices"] = max(0, maximum - active_count)
    ident["current_device_count"] = active_count
    ident["max_devices"] = maximum
    ident["total_devices"] = Device.objects.filter(user=user).count()
    return device, is_new, ident


def approve_current_device(request, user) -> Device:
    """Approve only the pending registration the requesting browser maps to.

    That is the browser's own fallback registration, or the registered OS it was
    recognised on (see ``DeviceBrowserLink``).
    """
    newly_trusted = False
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        device = find_current_device(request, user, for_update=True)
        if device is None:
            raise DevicePolicyError("device_not_found")
        if device.is_blocked:
            raise DevicePolicyError("blocked_device", {"device": device})
        if device.revoked_at is not None:
            raise DevicePolicyError("revoked_device", {"device": device})
        if not device.is_trusted:
            newly_trusted = True
            trust_pending_device(user, device, reason="current_browser_approved")
    if newly_trusted:
        notify_device_registered(user, device, get_client_ip(request) or "")
    return device


def trust_pending_device(user, device: Device, *, reason: str) -> None:
    """Approve ``device`` within the account allowance; the caller holds the user lock."""
    active_count = get_active_device_count(user)
    maximum = get_device_limit(user)
    if active_count >= maximum:
        raise DevicePolicyError(
            "limit_reached",
            {
                "device": device,
                "current_device_count": active_count,
                "max_devices": maximum,
            },
        )
    device.is_trusted = True
    device.save(update_fields=["is_trusted"])
    DeviceEvent.objects.create(
        device=device,
        user=user,
        event_type="device_trusted",
        success=True,
        reason=reason,
    )


def notify_device_registered(user, device: Device, ip: str) -> None:
    """Tell the user a device is now registered (approved); never for a pending one."""
    from apps.users.services.notifications import send_notification

    kind = "computer" if device.is_os_identity else "browser"
    label = device.display_name or f"{device.os_family or 'Unknown OS'} {kind}"
    send_notification(
        user,
        "Device registered",
        f"{label} is now registered to your account ({kind} registration, "
        f"approved from {ip or 'unknown IP'}).",
        level="info",
        channel="web",
    )


def enforce_device_policy_for_login(
    request, user, *, mfa_verified: bool = False
) -> tuple[bool, dict[str, Any]]:
    """
    Enforce device policy during login.
    """
    try:
        device, is_new, ctx = resolve_or_create_device(
            request, user, service_name="login"
        )
    except DevicePolicyError as exc:
        _log_event(
            None, user, "login", success=False, reason=exc.reason, ctx=exc.context or {}
        )
        raise

    if ctx.get("identity_level") != "primary":
        _log_event(
            device,
            user,
            "login",
            success=False,
            reason="fallback_identity_blocked",
            ctx=ctx,
        )
        raise DevicePolicyError("fallback_identity_blocked", {"device": device})

    if device and device.revoked_at is not None:
        _log_event(
            device, user, "login", success=False, reason="revoked_device", ctx=ctx
        )
        raise DevicePolicyError("revoked_device", {"device": device})

    if device and device.is_blocked:
        _log_event(
            device, user, "login", success=False, reason="blocked_device", ctx=ctx
        )
        _notify_user_device_issue(user, "blocked_device", device, ctx)
        raise DevicePolicyError("blocked_device", {"device": device})

    policy = ctx["policy_snapshot"]

    if (
        policy.get("strict_new_device_login")
        and device
        and not device.is_trusted
        and get_active_device_count(user) > 0
    ):
        _log_event(
            device, user, "login", success=False, reason="untrusted_new_device", ctx=ctx
        )
        _notify_user_device_issue(user, "untrusted_new_device", device, ctx)
        raise DevicePolicyError("untrusted_new_device", {"device": device})

    if policy.get("mfa_requirement") == "always" and not mfa_verified:
        _log_event(device, user, "login", success=False, reason="mfa_required", ctx=ctx)
        raise DevicePolicyError("mfa_required", {"device": device})

    if policy.get("require_mfa_on_new_device") and is_new and not mfa_verified:
        _log_event(device, user, "login", success=False, reason="mfa_required", ctx=ctx)
        _notify_user_device_issue(user, "mfa_required", device, ctx)
        raise DevicePolicyError("mfa_required", {"device": device})

    # Risk-based MFA escalation
    try:
        risk_threshold = int(policy.get("risk_mfa_threshold") or 0)
    except (TypeError, ValueError):
        risk_threshold = 0
    if (
        risk_threshold
        and device
        and getattr(device, "risk_score", 0) >= risk_threshold
        and not mfa_verified
    ):
        _log_event(
            device, user, "login", success=False, reason="mfa_required_risk", ctx=ctx
        )
        _notify_user_device_issue(user, "mfa_required_risk", device, ctx)
        raise DevicePolicyError(
            "mfa_required_risk",
            {"device": device, "risk_score": getattr(device, "risk_score", 0)},
        )

    _log_event(device, user, "login", success=True, reason="policy_pass", ctx=ctx)
    return True, {"device": device, "is_new": is_new, "context": ctx}


def enforce_device_policy_for_service(
    request,
    user,
    service_name: str,
) -> tuple[bool, dict[str, Any]]:
    """
    Enforce device policy for a named service.
    """
    try:
        device, is_new, ctx = resolve_or_create_device(
            request, user, service_name=service_name
        )
    except DevicePolicyError as exc:
        _log_event(
            None,
            user,
            service_name,
            success=False,
            reason=exc.reason,
            ctx=exc.context or {},
        )
        raise

    if ctx.get("identity_level") != "primary":
        _log_event(
            device,
            user,
            service_name,
            success=False,
            reason="fallback_identity_blocked",
            ctx=ctx,
        )
        raise DevicePolicyError("fallback_identity_blocked", {"device": device})
    policy = ctx["policy_snapshot"]
    service_rules = policy.get("service_rules") or {}

    if device and device.revoked_at is not None:
        _log_event(
            device,
            user,
            service_name,
            success=False,
            reason="revoked_device",
            ctx=ctx,
        )
        raise DevicePolicyError("revoked_device", {"device": device})

    if device and device.is_blocked:
        _log_event(
            device, user, service_name, success=False, reason="blocked_device", ctx=ctx
        )
        raise DevicePolicyError("blocked_device", {"device": device})

    if policy.get("require_mfa_on_new_device") and is_new:
        _log_event(
            device, user, service_name, success=False, reason="mfa_required", ctx=ctx
        )
        raise DevicePolicyError("mfa_required", {"device": device})

    try:
        risk_threshold = int(policy.get("risk_mfa_threshold") or 0)
    except (TypeError, ValueError):
        risk_threshold = 0
    if risk_threshold and device and getattr(device, "risk_score", 0) >= risk_threshold:
        _log_event(
            device,
            user,
            service_name,
            success=False,
            reason="mfa_required_risk",
            ctx=ctx,
        )
        raise DevicePolicyError(
            "mfa_required_risk",
            {"device": device, "risk_score": getattr(device, "risk_score", 0)},
        )

    if service_rules.get("trusted_device_only") and not (device and device.is_trusted):
        _log_event(
            device,
            user,
            service_name,
            success=False,
            reason="untrusted_device",
            ctx=ctx,
        )
        raise DevicePolicyError("untrusted_device", {"device": device})

    max_devices = service_rules.get("max_devices")
    if max_devices:
        count = get_active_device_count(user)
        if count > int(max_devices):
            _log_event(
                device,
                user,
                service_name,
                success=False,
                reason="service_device_limit",
                ctx=ctx,
            )
            raise DevicePolicyError("service_device_limit", {"device": device})

    _log_event(device, user, service_name, success=True, reason="policy_pass", ctx=ctx)
    return True, {"device": device, "is_new": is_new, "context": ctx}


def _log_event(
    device: Device | None,
    user,
    event_type: str,
    success: bool,
    reason: str,
    ctx: dict,
) -> None:
    payload = {
        "device": device,
        "user": user if getattr(user, "is_authenticated", False) else None,
        "event_type": event_type,
        "success": success,
        "reason": reason,
        "ip": ctx.get("ip", ""),
        "user_agent": ctx.get("user_agent", ""),
        "geo_region": ctx.get("country", ""),
        "metadata": {
            "policy": ctx.get("policy_snapshot"),
            "consent": ctx.get("consent_snapshot"),
        },
    }
    try:
        DeviceEvent.objects.create(**payload)
    except DatabaseError:
        logger.warning(
            "Failed to persist DeviceEvent; continuing without event",
            exc_info=True,
            extra={"device_id": getattr(device, "id", None), "event_type": event_type},
        )
        return
    if not success:
        _emit_security_event(
            f"device_{event_type}",
            user=payload.get("user"),
            device=device,
            ip=payload.get("ip"),
            metadata={
                "success": success,
                "reason": reason,
                "policy": ctx.get("policy_snapshot", {}),
            },
        )

    # A sign-in from a browser that is not registered yet is only *pending*: say so,
    # and stay silent when the browser may just be another one on a registered OS
    # (the OS check decides; a real registration notifies when it is approved).
    if (
        success
        and payload["user"]
        and ctx.get("is_new")
        and not ctx.get("os_check_pending")
    ):
        from apps.users.services.notifications import send_notification

        device_label = (
            getattr(device, "display_name", None)
            or f"{getattr(device, 'os_family', '') or 'Unknown OS'} browser"
        )
        send_notification(
            payload["user"],
            "New device awaiting approval",
            f"A new browser ({device_label}) signed in from "
            f"{payload.get('ip') or 'unknown IP'}. It is not registered yet and "
            "uses a device slot only after you approve it.",
            level="info",
            channel="web",
        )

    promote_from_device_event(
        {
            "device_identifier": str(device.pk) if device else None,
            "ip": payload["ip"],
            "user": payload["user"],
            "event_type": event_type,
            "success": success,
            "reason": reason,
        }
    )


def log_device_event(
    device: Device | None,
    user,
    event_type: str,
    *,
    success: bool,
    reason: str,
    ctx: dict[str, Any],
) -> None:
    """Public audit hook for the device-owned flows (OS registration/verification)."""
    _log_event(device, user, event_type, success=success, reason=reason, ctx=ctx)


def _emit_security_event(
    event_type: str,
    user=None,
    device=None,
    ip: str | None = None,
    metadata: dict | None = None,
):
    try:
        DeviceEvent.objects.create(
            event_type="policy_violation",
            user=user,
            device=device,
            ip=ip or "",
            success=False,
            reason=event_type[:255],
            metadata=metadata or {},
        )
    except DatabaseError:
        logger.exception(
            "Could not persist device policy event %s for user_id=%s",
            event_type,
            getattr(user, "pk", None),
        )
        return


def _notify_user_device_issue(
    user, reason: str, device: Device | None, ctx: dict[str, Any]
) -> None:
    """
    Notify the user when a device is blocked or needs trust/MFA.
    """
    if not getattr(user, "is_authenticated", False):
        return
    from apps.users.services.notifications import send_notification

    ip = ctx.get("ip") or "unknown IP"
    device_label = (
        getattr(device, "display_name", None)
        or getattr(device, "os_fingerprint", None)
        or "device"
    )
    if reason == "blocked_device":
        title = "Sign-in blocked"
        message = f"Your device ({device_label}) was blocked from signing in from {ip}. If this was you, review devices and unblock."
    elif reason == "untrusted_new_device":
        title = "New device requires trust"
        message = f"A new device ({device_label}) tried to sign in from {ip}. Trust it from an existing device if you recognize it."
    elif reason == "mfa_required":
        title = "Multi-factor required for new device"
        message = f"A new device ({device_label}) requires MFA to finish signing in. Complete MFA or trust it from an existing device."
    elif reason == "mfa_required_risk":
        title = "Multi-factor required (risky device)"
        message = f"This device ({device_label}) scored high risk from {ip}. Complete MFA or trust it from an existing device to continue."
    else:
        return

    send_notification(
        recipient=user,
        title=title,
        message=message,
        level="warning",
        channel="web",
    )


# ---------------------------------------------------------------------------
# Risk and anomaly insights
# ---------------------------------------------------------------------------


def record_insight(
    *,
    related_user=None,
    device_identifier: str | None = None,
    related_ip: str | None = None,
    severity: str = "low",
    recommendation: str = "",
    metadata: dict[str, Any] | None = None,
) -> BehaviorInsight:
    """Minimal ingestion point for risk/anomaly insights."""
    from apps.devices.models import BehaviorInsight  # local to avoid circular

    details = dict(metadata or {})
    if device_identifier:
        details["device_identifier"] = device_identifier
    if related_ip:
        details["ip"] = related_ip
    return BehaviorInsight.objects.create(
        related_user=related_user,
        severity=severity,
        recommendation=recommendation,
        metadata=details,
    )


def promote_from_device_event(event: dict[str, Any]) -> BehaviorInsight | None:
    """Flag failed or blocked device events; successful events create no insight."""
    if event.get("success"):
        return None
    reason = str(event.get("reason") or "")
    if reason in {"blocked_device", "policy_violation"}:
        severity = "high"
        recommendation = "Block or review device."
    else:
        severity = "medium"
        recommendation = "Ask for MFA on next login."

    user = event.get("user")
    return record_insight(
        related_user=user if getattr(user, "pk", None) else None,
        device_identifier=event.get("device_identifier"),
        related_ip=event.get("ip"),
        severity=severity,
        recommendation=recommendation,
        metadata={
            "event_type": event.get("event_type"),
            "reason": reason,
        },
    )
