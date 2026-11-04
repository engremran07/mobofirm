from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core import signing
from django.http import HttpResponse
from django.utils import timezone

from apps.core.utils.ip import get_client_ip

if TYPE_CHECKING:
    from apps.devices.models import BehaviorInsight
from apps.devices.models import AppPolicy, Device, DeviceConfig, DeviceEvent
from apps.devices.models_quota import UserDeviceQuota
from apps.devices.utils.device_fingerprint import make_os_fingerprint

logger = logging.getLogger(__name__)

Policy = dict[str, Any]
Identity = dict[str, Any]


class DevicePolicyError(Exception):
    def __init__(self, reason: str, context: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.context = context or {}


def _hash_server_fingerprint(ua: str, ip: str, session_key: str) -> str:
    payload = f"{ua}|{ip}|{session_key}".encode()
    return hashlib.sha256(payload).hexdigest()


def _get_or_set_fallback_id(request, ua: str, ip: str) -> str:
    """
    Stable fallback identifier stored in session to avoid churn on session key rotation.
    """
    session = getattr(request, "session", None)
    try:
        if session is not None:
            existing = session.get("device_fallback_id")
            if existing:
                return existing
            new_id = _hash_server_fingerprint(ua, ip, str(uuid.uuid4()))
            session["device_fallback_id"] = new_id
            return new_id
    except Exception:  # noqa: S110
        pass
    return _hash_server_fingerprint(ua, ip, str(uuid.uuid4()))


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


def make_device_token(user_id, device_id, reason: str) -> str:
    payload = {"u": str(user_id), "d": str(device_id), "r": reason}
    return signing.dumps(payload, salt="device-approval")


def load_device_token(token: str, max_age: int = 86400) -> dict[str, str] | None:
    try:
        data = signing.loads(token, max_age=max_age, salt="device-approval")
        if isinstance(data, dict) and data.get("u") and data.get("d"):
            return data
    except Exception:
        return None
    return None


def mark_device_trusted(device_id, user_id) -> bool:
    try:
        from apps.devices.models import Device

        device = Device.objects.filter(id=device_id, user_id=user_id).first()
        if not device:
            return False
        device.is_trusted = True
        device.is_blocked = False
        device.save(update_fields=["is_trusted", "is_blocked"])
        return True
    except Exception:
        return False


def attach_device_cookie(
    response: HttpResponse, device: Device, max_age_days: int = 365
) -> HttpResponse:
    """
    Persist the OS fingerprint for convenience (non-authoritative). This is a helper only;
    the primary identity comes from the deterministic OS fingerprint hash.
    """
    try:
        if response and device and getattr(device, "os_fingerprint", None):
            response.set_cookie(
                "os_fingerprint",
                device.os_fingerprint,
                max_age=max_age_days * 24 * 3600,
                httponly=False,
                secure=True,
                samesite="Lax",
            )
    except Exception:
        logger.debug("attach_device_cookie failed", exc_info=True)
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
        "max_devices": int(getattr(config, "max_devices_default", 5) or 5),
        "monthly_quota": getattr(config, "monthly_device_quota", None),
        "yearly_quota": getattr(config, "yearly_device_quota", None),
        "ad_unlock_enabled": bool(getattr(config, "ad_unlock_enabled", False)),
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
                "ad_unlock_enabled": app_policy.ad_unlock_enabled
                or policy["ad_unlock_enabled"],
            }
        )

    # -----------------------------------------------------------------
    # SUPER ADMIN OVERRIDE (Unlimited Access)
    # -----------------------------------------------------------------
    # Superusers get unlimited devices to avoid lockout scenarios.
    # Regular staff/users are subject to normal policy limits.
    if user and user.is_authenticated and user.is_superuser:
        policy["max_devices"] = 999999
        policy["monthly_quota"] = None
        policy["yearly_quota"] = None

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
    """
    Resolve device identity using OS fingerprint inputs (UA + JS payload).
    """
    policy = get_effective_policy(request, user=user, service_name=service_name)
    consent = getattr(request, "consent_categories", {}) or {}
    ip = get_client_ip(request) or ""
    country = (
        request.META.get("HTTP_CF_IPCOUNTRY")
        or request.META.get("GEOIP_COUNTRY_CODE")
        or request.META.get("HTTP_X_COUNTRY_CODE")
        or ""
    ).strip()
    ua = (request.META.get("HTTP_USER_AGENT") or "").strip()

    payload = getattr(request, "device_payload", None) or {}
    identity_level = "primary"
    return {
        "payload": payload,
        "identity_level": identity_level,
        "policy_snapshot": policy,
        "consent_snapshot": consent,
        "ip": ip,
        "country": country,
        "user_agent": ua,
    }


def resolve_or_create_device(
    request,
    user,
    service_name: str | None = None,
) -> tuple[Device | None, bool, dict[str, Any]]:
    """
    Resolve an existing device or create a new one subject to policy rules.
    Returns (device, is_new, context).
    """
    ident = resolve_identity(request, user=user, service_name=service_name)
    policy = ident["policy_snapshot"]
    now = timezone.now()
    payload = ident.get("payload") or {}
    ua = ident.get("user_agent", "") or ""
    ip = ident.get("ip", "") or ""
    monthly_quota = policy.get("monthly_quota")
    yearly_quota = policy.get("yearly_quota")
    override = None

    device = None
    is_new = False
    prev_event_ip = None
    prev_country = None
    try:
        last_evt = (
            DeviceEvent.objects.filter(user=user)
            .order_by("-created_at")
            .only("ip", "geo_region")
            .first()
        )
        prev_event_ip = getattr(last_evt, "ip", None)
        prev_country = getattr(last_evt, "geo_region", None)
    except Exception:
        prev_event_ip = None
        prev_country = None

    # Compute deterministic OS fingerprint
    os_fp, os_info = make_os_fingerprint(user.id, ua, payload)
    if not os_fp:
        if not policy.get("allow_server_fallback", True):
            raise DevicePolicyError("device_key_required", {"policy": policy})
        os_fp = _get_or_set_fallback_id(request, ua, ip)
        ident["identity_level"] = "fallback"
    ident["os_fingerprint"] = os_fp

    # Expire stale devices if configured
    expiry_days = policy.get("device_expiry_days")
    if expiry_days:
        cutoff = now - timedelta(days=int(expiry_days))
        Device.objects.filter(user=user, last_seen_at__lt=cutoff).delete()

    device = Device.objects.filter(user=user, os_fingerprint=os_fp).first()
    override = UserDeviceQuota.objects.filter(user=user).first()

    if device:
        updates = ["last_seen_at"]
        device.last_seen_at = now
        device.os_name = os_info.name or device.os_name
        device.os_version = os_info.version or device.os_version
        device.browser_family = _parse_browser_family(ua or device.browser_family)
        device.os_family = _parse_os_family(ua or device.os_family)
        updates.extend(["os_name", "os_version", "browser_family", "os_family"])

        try:
            # merge payload into metadata without losing existing fields
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            if payload:
                metadata = {**metadata, "payload": payload}
                device.metadata = metadata
                updates.append("metadata")
        except Exception:  # noqa: S110
            pass
        device.save(update_fields=list(set(updates)))
    else:
        # override = UserDeviceQuota.objects.filter(user=user).first()  <-- Moved up

        # Enforce monthly/yearly quotas before creating
        if monthly_quota:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_count = Device.objects.filter(
                user=user, is_blocked=False, first_seen_at__gte=month_start
            ).count()
            if month_count >= int(monthly_quota):
                raise DevicePolicyError("monthly_device_quota", {"policy": policy})
        if yearly_quota:
            year_start = now.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            year_count = Device.objects.filter(
                user=user, is_blocked=False, first_seen_at__gte=year_start
            ).count()
            if year_count >= int(yearly_quota):
                raise DevicePolicyError("yearly_device_quota", {"policy": policy})

        # Enforce user-specific rolling window quota if configured
        if override:
            window_map = {"3m": 90, "6m": 180, "12m": 365}
            days = window_map.get(override.window, 180)
            window_start = max(override.last_reset_at, now - timedelta(days=days))
            registrations = Device.objects.filter(
                user=user, is_blocked=False, first_seen_at__gte=window_start
            ).count()
            limit = (
                override.max_devices
                if override.max_devices is not None
                else policy.get("max_devices") or 5
            )
            if registrations >= int(limit):
                _emit_security_event(
                    "device_quota_exceeded",
                    user=user,
                    device=None,
                    ip=ident.get("ip"),
                    metadata={"window_days": days, "limit": limit},
                )
                raise DevicePolicyError(
                    "user_window_quota", {"policy": policy, "window_days": days}
                )

        # Enforce global rolling quota when enabled in SecurityConfig (only if no per-user override)
        if not override:
            quota_policy = {
                "enforcement_enabled": getattr(
                    settings, "DEVICE_QUOTA_ENFORCEMENT_ENABLED", False
                ),
                "window_days": getattr(settings, "DEVICE_QUOTA_WINDOW_DAYS", 365),
                "default_window": getattr(
                    settings, "DEVICE_QUOTA_DEFAULT_WINDOW", "12m"
                ),
                "default_limit": getattr(
                    settings, "DEVICE_QUOTA_DEFAULT_LIMIT", policy.get("max_devices", 5)
                ),
            }
            if quota_policy.get("enforcement_enabled"):
                window_days = int(quota_policy.get("window_days", 365))
                quota_record, _ = UserDeviceQuota.objects.get_or_create(
                    user=user,
                    defaults={
                        "window": quota_policy.get("default_window", "12m"),
                        "max_devices": None,
                        "last_reset_at": now,
                    },
                )
                window_start = max(
                    quota_record.last_reset_at,
                    now - timedelta(days=window_days),
                )
                registrations = Device.objects.filter(
                    user=user, is_blocked=False, first_seen_at__gte=window_start
                ).count()
                limit = quota_policy.get(
                    "default_limit", policy.get("max_devices") or 5
                )
                if registrations >= int(limit):
                    _emit_security_event(
                        "device_quota_exceeded",
                        user=user,
                        device=None,
                        ip=ident.get("ip"),
                        metadata={"window_days": window_days, "limit": limit},
                    )
                    raise DevicePolicyError(
                        "device_quota_exceeded",
                        {"policy": policy, "window_days": window_days},
                    )

        # Enforce max devices (global + per-user override)
        max_devices = int(
            override.max_devices
            if override and override.max_devices is not None
            else policy.get("max_devices") or 5
        )
        current_count = Device.objects.filter(user=user, is_blocked=False).count()
        if current_count >= max_devices:
            if policy.get("device_locking_mode") == "strict":
                raise DevicePolicyError("limit_reached", {"policy": policy})
            # soft mode: evict oldest
            oldest = (
                Device.objects.filter(user=user, is_blocked=False)
                .order_by("last_seen_at")
                .first()
            )
            if oldest:
                try:
                    _log_event(
                        oldest,
                        user,
                        service_name or "login",
                        success=False,
                        reason="evicted_oldest",
                        ctx=ident,
                    )
                    from apps.users.services.notifications import send_notification

                    send_notification(
                        user,
                        "Old device removed",
                        f"The oldest device ({getattr(oldest, 'display_name', '') or getattr(oldest, 'os_fingerprint', '')}) was removed to make room for a new sign-in.",
                        level="info",
                        channel="web",
                    )
                except Exception:
                    logger.debug("Failed to log/notify eviction", exc_info=True)
                oldest.delete()
        device = Device.objects.create(
            user=user,
            os_fingerprint=os_fp,
            os_name=os_info.name,
            os_version=os_info.version,
            hardware_uuid=os_fp,  # legacy compatibility
            browser_family=_parse_browser_family(ua),
            os_family=_parse_os_family(ua),
            metadata={"payload": payload} if payload else {},
            first_seen_at=now,
            last_seen_at=now,
            # New devices require user approval via popup
            is_trusted=False,
        )
        is_new = True

    # Simple risk scoring heuristic
    try:
        risk = 0
        country = ident.get("country", "") or ""
        if is_new:
            risk += 40
        if ident.get("identity_level") != "primary":
            risk += 10
        if prev_event_ip and ip and prev_event_ip != ip:
            risk += 15
        if prev_country and country and prev_country != country:
            risk += 15
        if ua and device and device.metadata:
            # compare UA family
            prev_ua = (
                device.metadata.get("ua_family")
                if isinstance(device.metadata, dict)
                else None
            )
            if prev_ua and prev_ua != ua:
                risk += 10
        risk = max(0, min(100, risk))
        if device and risk != device.risk_score:
            device.risk_score = risk
            # persist UA family for future comparisons
            if isinstance(device.metadata, dict):
                device.metadata["ua_family"] = ua
                if country:
                    device.metadata["country"] = country
            device.save(update_fields=["risk_score", "metadata"])
        ident["risk_score"] = risk
    except Exception:
        ident["risk_score"] = getattr(device, "risk_score", 0) if device else 0

    ident["is_new"] = is_new
    # Remaining quota/slots snapshot for UX
    try:
        allowed_max = int(
            override.max_devices
            if override and override.max_devices is not None
            else policy.get("max_devices") or 5
        )
        current_count = Device.objects.filter(user=user, is_blocked=False).count()
        ident["remaining_devices"] = max(0, allowed_max - current_count)
        ident["current_device_count"] = current_count
        ident["max_devices"] = allowed_max
    except Exception:
        ident["remaining_devices"] = None
        ident["current_device_count"] = 0
        ident["max_devices"] = 5

    try:
        reset_days: int | None = None
        if monthly_quota:
            month_end = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
            reset_days = max(0, (month_end - now).days)
        elif yearly_quota:
            year_end = now.replace(
                month=12, day=31, hour=23, minute=59, second=59, microsecond=0
            )
            reset_days = max(0, (year_end - now).days)
        elif override:
            window_map = {"3m": 90, "6m": 180, "12m": 365}
            days = window_map.get(override.window, 180)
            window_start = max(override.last_reset_at, now - timedelta(days=days))
            reset_days = max(0, (window_start + timedelta(days=days) - now).days)
        ident["quota_reset_days"] = reset_days
    except Exception:
        ident["quota_reset_days"] = None
    return device, is_new, ident


def enforce_device_policy_for_login(request, user) -> tuple[bool, dict[str, Any]]:
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

    if device and device.is_blocked:
        _log_event(
            device, user, "login", success=False, reason="blocked_device", ctx=ctx
        )
        _notify_user_device_issue(user, "blocked_device", device, ctx)
        raise DevicePolicyError("blocked_device", {"device": device})

    policy = ctx["policy_snapshot"]

    if policy.get("strict_new_device_login") and device and not device.is_trusted:
        _log_event(
            device, user, "login", success=False, reason="untrusted_new_device", ctx=ctx
        )
        _notify_user_device_issue(user, "untrusted_new_device", device, ctx)
        raise DevicePolicyError("untrusted_new_device", {"device": device})

    if policy.get("require_mfa_on_new_device") and is_new:
        _log_event(device, user, "login", success=False, reason="mfa_required", ctx=ctx)
        _notify_user_device_issue(user, "mfa_required", device, ctx)
        raise DevicePolicyError("mfa_required", {"device": device})

    # Risk-based MFA escalation
    try:
        risk_threshold = int(policy.get("risk_mfa_threshold") or 0)
    except Exception:
        risk_threshold = 0
    if risk_threshold and device and getattr(device, "risk_score", 0) >= risk_threshold:
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

    if device and device.is_blocked:
        _log_event(
            device, user, service_name, success=False, reason="blocked_device", ctx=ctx
        )
        raise DevicePolicyError("blocked_device", {"device": device})

    if (
        policy.get("strict_new_device_login")
        and is_new
        and not (device and device.is_trusted)
    ):
        _log_event(
            device,
            user,
            service_name,
            success=False,
            reason="untrusted_new_device",
            ctx=ctx,
        )
        raise DevicePolicyError("untrusted_new_device", {"device": device})

    if policy.get("require_mfa_on_new_device") and is_new:
        _log_event(
            device, user, service_name, success=False, reason="mfa_required", ctx=ctx
        )
        raise DevicePolicyError("mfa_required", {"device": device})

    try:
        risk_threshold = int(policy.get("risk_mfa_threshold") or 0)
    except Exception:
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
        count = Device.objects.filter(user=user, is_blocked=False).count()
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
    except Exception:  # pragma: no cover - defensive path
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

    # Notify user when a new device registers successfully
    try:
        if success and payload["user"] and ctx.get("is_new"):
            from apps.users.services.notifications import send_notification

            send_notification(
                payload["user"],
                "New device registered",
                f"A new device ({getattr(device, 'os_fingerprint', 'unknown')}) was added from {payload.get('ip') or 'unknown IP'}.",
                level="warning",
                channel="web",
            )
    except Exception:  # noqa: S110
        pass

    # Forward to AI behavior engine directly
    try:
        promote_from_device_event(
            {
                "device_identifier": getattr(device, "os_fingerprint", None),
                "ip": payload["ip"],
                "user": payload["user"],
                "event_type": event_type,
                "success": success,
                "reason": reason,
                "metadata": payload.get("metadata", {}),
            }
        )
    except Exception:  # noqa: S110
        pass


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
    except Exception:
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
    try:
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
    except Exception:
        logger.debug("notify user device issue skipped", exc_info=True)


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

    return BehaviorInsight.objects.create(
        related_user=related_user,
        device_identifier=device_identifier,
        related_ip=related_ip,
        severity=severity,
        recommendation=recommendation,
        metadata=metadata or {},
        created_at=timezone.now(),
    )


def promote_from_device_event(event: dict[str, Any]) -> BehaviorInsight | None:
    """Lightweight heuristic: flag blocked/repeated failures as high severity."""
    try:
        severity = "low"
        recommendation = "Monitor device activity."
        if event.get("reason") in {"blocked_device", "policy_violation"}:
            severity = "high"
            recommendation = "Block or review device."
        elif not event.get("success"):
            severity = "medium"
            recommendation = "Ask for MFA on next login."

        return record_insight(
            related_user=event.get("user"),
            device_identifier=event.get("device_identifier"),
            related_ip=event.get("ip"),
            severity=severity,
            recommendation=recommendation,
            metadata=event,
        )
    except Exception:
        return None
