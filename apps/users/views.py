"""
apps.users.views
Enterprise-grade user management and authentication views for the platform.

✅ Highlights
-------------
• Tenant-aware SiteSettings resolver (uses site_settings.views._get_settings when available)
• Integrated rate limiting + reCAPTCHA verification
• MFA / Email verification enforcement
• Optimized dashboard queries (deferred, select_related)
• Atomic safety and hardened UX
• Fully compatible with Django 5.x and allauth ≥ 0.65
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, cast

from allauth.account.forms import LoginForm, SignupForm
from allauth.account.views import LoginView, SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.sites.shortcuts import get_current_site
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.app_service import AppService
from apps.core.utils.ip import get_client_ip
from apps.devices.services import (
    DevicePolicyError,
    attach_device_cookie,
    load_device_token,
    make_device_token,
    mark_device_trusted,
)
from apps.users.forms import TellUsAboutYouForm
from apps.users.models import Announcement, CustomUser, Notification
from apps.users.services.rate_limit import allow_action
from apps.users.services.recaptcha import verify_recaptcha

logger = logging.getLogger(__name__)


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """
    Display the authenticated user's profile.
    """
    # Gamification data
    gamification_data: dict[str, Any] = {}
    uid = cast(int, request.user.pk)  # @login_required guarantees authenticated user
    try:
        from apps.gamification.services import get_current_level, get_total_xp

        total_xp = get_total_xp(uid)
        current_level = get_current_level(uid)
        gamification_data["total_xp"] = total_xp
        gamification_data["level"] = current_level
    except Exception:  # noqa: S110
        pass
    try:
        from apps.gamification.models import Badge, Streak, UserBadge

        user_badges = (
            UserBadge.objects.filter(user=request.user)
            .select_related("badge")
            .order_by("-earned_at")[:10]
        )
        gamification_data["badges"] = user_badges
        gamification_data["badge_count"] = user_badges.count()
        gamification_data["total_badges"] = Badge.objects.filter(is_active=True).count()
        streak = Streak.objects.filter(user=request.user).first()
        gamification_data["streak"] = streak
    except Exception:  # noqa: S110
        pass

    # Referral stats
    referral_stats: dict[str, int] = {"clicks": 0, "conversions": 0}
    user_referral_code = ""
    try:
        from apps.referral.models import ReferralCode

        rc = ReferralCode.objects.filter(user=request.user, is_active=True).first()
        if rc:
            user_referral_code = rc.code
            referral_stats = {"clicks": rc.clicks, "conversions": rc.conversions}
    except Exception:  # noqa: S110
        pass

    # Bounty stats
    bounty_stats: dict[str, int] = {"created": 0, "fulfilled": 0}
    try:
        from apps.bounty.models import BountyRequest, BountySubmission

        bounty_stats["created"] = BountyRequest.objects.filter(
            user=request.user
        ).count()
        bounty_stats["fulfilled"] = BountySubmission.objects.filter(
            user=request.user, status="accepted"
        ).count()
    except Exception:  # noqa: S110
        pass

    # Reputation
    reputation_obj = None
    try:
        from apps.user_profile.models import Reputation

        reputation_obj = Reputation.objects.filter(user=request.user).first()
    except Exception:  # noqa: S110
        pass

    context: dict[str, Any] = {
        "user": request.user,
        "notifications": Notification.objects.filter(recipient=request.user).order_by(
            "-created_at"
        )[:10],
        "announcements": Announcement.objects.filter(is_active=True).order_by(
            "-created_at"
        )[:5],
        "gamification": gamification_data,
        "referral_code": user_referral_code,
        "referral_stats": referral_stats,
        "bounty_stats": bounty_stats,
        "reputation": reputation_obj,
    }
    return render(request, "users/profile.html", context)


@login_required
@csrf_protect
def devices_view(request: HttpRequest) -> HttpResponse:
    """
    List and manage the current user's devices (trust/untrust/delete).
    """
    devices = []
    message = ""
    error = ""
    pending_device = None
    try:
        from apps.devices.models import Device
    except Exception:
        Device = None

    if Device is None:
        error = "Device management is unavailable."
    else:
        if request.method == "POST":
            action = request.POST.get("action") or ""
            if action == "clear_device_prompt":
                request.session.pop("pending_device_prompt_uuid", None)
                return redirect("users:devices")
            device_id = request.POST.get("device_id") or ""
            try:
                device = Device.objects.filter(user=request.user, id=device_id).first()
                if not device:
                    error = "Device not found."
                else:
                    # Only staff/superusers may change trust state or delete devices
                    if not getattr(request.user, "is_staff", False) and not getattr(
                        request.user, "is_superuser", False
                    ):
                        error = "You cannot manually remove devices. Please wait for the automatic reset."
                    else:
                        if action == "trust":
                            device.is_trusted = True
                            device.save(update_fields=["is_trusted"])
                            message = "Device trusted."
                        elif action == "untrust":
                            device.is_trusted = False
                            device.save(update_fields=["is_trusted"])
                            message = "Device untrusted."
                        elif action == "delete":
                            if device.is_trusted and not getattr(
                                request.user, "is_superuser", False
                            ):
                                error = "Trusted devices cannot be removed directly. Untrust first or ask an admin."
                            else:
                                device.delete()
                                message = "Device removed."
            except Exception as exc:
                error = f"Action failed: {exc}"

        try:
            # Group devices by OS fingerprint to show "Same Device" status
            raw_devices = list(
                Device.objects.filter(user=request.user)
                .order_by("-last_seen_at")
                .values(
                    "id",
                    "os_fingerprint",
                    "display_name",
                    "browser_family",
                    "os_family",
                    "is_trusted",
                    "is_blocked",
                    "risk_score",
                    "last_seen_at",
                    "first_seen_at",
                )
            )

            # Post-process to flag duplicates (same OS, different browser entry)
            seen_fingerprints = {}
            devices = []
            for d in raw_devices:
                fp = d["os_fingerprint"]
                if fp and fp in seen_fingerprints:
                    d["is_duplicate_entry"] = True
                    d["primary_device_id"] = seen_fingerprints[fp]
                else:
                    d["is_duplicate_entry"] = False
                    if fp:
                        seen_fingerprints[fp] = d["id"]
                devices.append(d)

        except Exception as exc:
            error = f"Could not load devices: {exc}"

        # Surface any pending device approval token so the user can complete registration/trust
        try:
            token = request.session.get("pending_device_token")
            reason = request.session.get("pending_device_reason", "")
            if token:
                pending_device = {
                    "token": token,
                    "reason": reason,
                    "approval_url": reverse("users:approve_device") + f"?t={token}",
                }
        except Exception:
            pending_device = None

    # Calculate quota info for display
    remaining_devices = None
    max_devices = 5
    current_device_count = len(devices)
    try:
        from apps.devices.services import get_effective_policy

        policy = get_effective_policy(request, user=request.user)
        max_devices = policy.get("max_devices", 5)
        current_device_count = Device.objects.filter(  # type: ignore[union-attr]
            user=request.user, is_blocked=False
        ).count()
        remaining_devices = max(0, max_devices - current_device_count)
    except Exception:  # noqa: S110
        pass

    return render(
        request,
        "users/devices.html",
        {
            "devices": devices,
            "message": message,
            "error": error,
            "pending_device": pending_device,
            "remaining_devices": remaining_devices,
            "max_devices": max_devices,
            "current_device_count": current_device_count,
        },
    )


def login_view(request: HttpRequest) -> HttpResponse:
    """
    Render the login page. Delegates authentication to django-allauth.
    """
    if request.user.is_authenticated:
        return redirect("core:home")

    context: dict[str, Any] = {
        "form": LoginForm(),
        "site": get_current_site(request),
    }
    return render(request, "account/login.html", context)


# ============================================================
# Settings resolver (lazy import to avoid circular deps)
# ============================================================
def _get_settings(request: HttpRequest | None = None) -> dict[str, object]:
    """
    Return primitive settings snapshot (dict). Try to use the canonical resolver
    from apps.site_settings.views (which already returns dict snapshots). If
    unavailable, fall back to safe defaults.
    """
    try:
        from apps.site_settings.models import SiteSettings
        from apps.users.models import UsersSettings

        us = UsersSettings.get_solo()
        ss = SiteSettings.get_solo()
        return {
            "site_name": "the platform",
            "enable_signup": bool(getattr(us, "enable_signup", True)),
            "max_login_attempts": int(getattr(us, "max_login_attempts", 5) or 5),
            "rate_limit_window_seconds": int(
                getattr(us, "rate_limit_window_seconds", 300) or 300
            ),
            # reCAPTCHA now comes from SiteSettings
            "recaptcha_enabled": bool(getattr(ss, "recaptcha_enabled", False)),
            "recaptcha_mode": getattr(ss, "recaptcha_mode", "v2"),
            "recaptcha_score_threshold": float(
                getattr(ss, "recaptcha_score_threshold", 0.5)
            ),
            "recaptcha_timeout_ms": int(getattr(ss, "recaptcha_timeout_ms", 3000)),
            "require_mfa": bool(getattr(us, "require_mfa", False)),
            "enable_payments": bool(getattr(us, "enable_payments", True)),
            "required_profile_fields": list(
                getattr(us, "required_profile_fields", []) or []
            ),
        }
    except Exception:
        logger.debug("UsersSettings fallback defaults in use", exc_info=True)
        return {
            "site_name": "the platform",
            "enable_signup": True,
            "max_login_attempts": 5,
            "rate_limit_window_seconds": 300,
            "recaptcha_enabled": False,
            "require_mfa": False,
            "enable_payments": True,
            "site_header": "GSM Admin",
            "site_description": "Default configuration",
            "meta_tags": [],
            "verification_files": [],
            # Branding fallbacks used by base.html
            "primary_color": "#0d6efd",
            "secondary_color": "#6c757d",
            "logo": None,
            "dark_logo": None,
            "favicon": None,
            "required_profile_fields": ["full_name", "username", "email"],
        }


def _get_platform_features() -> list[dict[str, str]]:
    """Return the list of platform features unlocked by profile completion."""
    return [
        {
            "icon": "download",
            "title": "Firmware Downloads",
            "description": "Download official firmware files for any device, with high-speed servers and resume support.",
            "app": "Firmwares",
            "url": "/firmwares/",
        },
        {
            "icon": "upload",
            "title": "Firmware Contributions",
            "description": "Upload and share firmware files to help the community. Earn reputation points.",
            "app": "Firmwares",
            "url": "/firmwares/upload/",
        },
        {
            "icon": "shield-check",
            "title": "Trusted Tester Programme",
            "description": "Apply to become a Trusted Tester — verify firmware, write test reports, and earn credits.",
            "app": "Firmwares",
            "url": "/firmwares/api/trusted-testers/",
        },
        {
            "icon": "file-search",
            "title": "Firmware Requests",
            "description": "Request missing firmware and get notified when community members or scrapers find it.",
            "app": "Firmwares",
            "url": "/firmwares/",
        },
        {
            "icon": "target",
            "title": "Bounty System",
            "description": "Create bounties for hard-to-find firmware or fulfill bounties posted by others for rewards.",
            "app": "Bounty",
            "url": "/api/v1/bounty/requests/",
        },
        {
            "icon": "store",
            "title": "Marketplace",
            "description": "Buy and sell firmware, tools, and device resources in the peer-to-peer marketplace.",
            "app": "Marketplace",
            "url": "/api/v1/marketplace/listings/",
        },
        {
            "icon": "wallet",
            "title": "Wallet & Credits",
            "description": "Manage your balance, earn credits through contributions, and withdraw earnings.",
            "app": "Wallet",
            "url": "/api/v1/wallet/wallets/",
        },
        {
            "icon": "users",
            "title": "Referral Programme",
            "description": "Generate referral codes, share with friends, and earn commission on subscriptions.",
            "app": "Referral",
            "url": "/api/v1/referral/codes/",
        },
        {
            "icon": "trophy",
            "title": "Gamification & Badges",
            "description": "Earn XP from every action, unlock achievement badges, and climb the leaderboard.",
            "app": "Gamification",
            "url": "/api/v1/gamification/badges/",
        },
        {
            "icon": "message-circle",
            "title": "Comments & Discussions",
            "description": "Comment on firmware pages, vote on helpful reviews, and engage with the community.",
            "app": "Comments",
            "url": "/blog/",
        },
        {
            "icon": "pen-tool",
            "title": "Blog & Articles",
            "description": "Write and publish tech articles, firmware guides, and how-to posts for the community.",
            "app": "Blog",
            "url": "/blog/",
        },
        {
            "icon": "shopping-cart",
            "title": "Shop & Subscriptions",
            "description": "Subscribe for premium downloads, faster speeds, and higher daily quotas with no ads.",
            "app": "Shop",
            "url": "/api/v1/shop/plans/",
        },
        {
            "icon": "tv",
            "title": "Rewarded Ads",
            "description": "Watch short ads to unlock additional free downloads and earn bonus credits.",
            "app": "Ads",
            "url": "/ads/api/placements/",
        },
        {
            "icon": "star",
            "title": "Reputation System",
            "description": "Build your reputation score through contributions, reviews, and community engagement.",
            "app": "Profile",
            "url": "/api/v1/profile/profiles/",
        },
        {
            "icon": "user-plus",
            "title": "Social Profile & Followers",
            "description": "Create a public profile, follow other members, and build your activity feed.",
            "app": "Profile",
            "url": "/api/v1/profile/profiles/",
        },
    ]


# ============================================================
# Enterprise Login View
# ============================================================
class EnterpriseLoginView(LoginView):
    """
    Enterprise login with:
    - IP-based rate limiting
    - reCAPTCHA verification
    - Optional MFA redirect
    """

    form_class = LoginForm
    template_name = "account/login.html"

    def form_valid(self, form):
        settings_obj = _get_settings(self.request)
        ip = get_client_ip(self.request) or "unknown"
        enforcement_ctx = None

        # --- Rate Limiting ---
        # FINDING-0016: fail_open=False — login must block when Redis is unavailable.
        try:
            if not allow_action(
                f"login:{ip}",
                int(str(settings_obj.get("max_login_attempts", 5))),
                int(str(settings_obj.get("rate_limit_window_seconds", 300))),
                fail_open=False,
            ):
                form.add_error(None, "Too many login attempts. Please try again later.")
                logger.warning("Rate limit exceeded for IP=%s", ip)
                return self.form_invalid(form)
        except Exception:
            logger.exception("Rate limiter failure (fail-open)")

        # --- reCAPTCHA ---
        token = self.request.POST.get("g-recaptcha-response") or self.request.POST.get(
            "recaptcha_token"
        )
        if settings_obj.get("recaptcha_enabled", False) and token:
            try:
                rc_result = verify_recaptcha(token, ip, action="login")
                if not rc_result.get("ok"):
                    form.add_error(
                        None, "reCAPTCHA verification failed. Please try again."
                    )
                    logger.info("reCAPTCHA failed for %s : %s", ip, rc_result)
                    return self.form_invalid(form)
            except Exception:
                logger.exception("reCAPTCHA error (fail-open)", exc_info=True)
                form.add_error(None, "reCAPTCHA service error. Try again later.")
                return self.form_invalid(form)

        response = super().form_valid(form)

        # --- Device policy enforcement (if devices app is enabled) ---
        try:
            devices_api = AppService.get("devices")
            if devices_api and hasattr(devices_api, "enforce_device_policy_for_login"):
                try:
                    _allowed, ctx = devices_api.enforce_device_policy_for_login(
                        self.request, self.request.user
                    )
                    enforcement_ctx = ctx or {}
                    self.request.device = enforcement_ctx.get("device")  # type: ignore[attr-defined]
                    try:
                        attach_device_cookie(response, enforcement_ctx.get("device"))  # type: ignore[arg-type]
                    except Exception:
                        logger.debug("attach_device_cookie failed", exc_info=True)
                    if enforcement_ctx.get("is_new"):
                        remaining = enforcement_ctx.get("context", {}).get(
                            "remaining_devices"
                        )
                        reset_days = enforcement_ctx.get("context", {}).get(
                            "quota_reset_days"
                        )
                        suffix = ""
                        if remaining is not None:
                            suffix += f" Remaining device slots: {remaining}."
                        if reset_days is not None:
                            suffix += f" Quotas reset in ~{reset_days} days."
                        messages.info(
                            self.request,
                            f"New device detected and registered.{suffix} Trust it from your devices page to avoid future prompts.",
                        )
                except DevicePolicyError as exc:
                    reason = exc.reason
                    device_obj = (exc.context or {}).get("device")
                    approval_token = None
                    try:
                        if device_obj:
                            approval_token = make_device_token(
                                self.request.user.pk,
                                device_obj.id,
                                reason or "untrusted_new_device",
                            )
                            self.request.session["pending_device_token"] = (
                                approval_token
                            )
                            self.request.session["pending_device_reason"] = reason
                    except Exception:
                        approval_token = None
                    if reason == "blocked_device":
                        msg = "This device is blocked. Contact support to unblock."
                    elif reason == "device_key_required":
                        msg = "Device fingerprint is required. Allow device fingerprinting and refresh to continue."
                    elif reason == "untrusted_new_device":
                        msg = "New device detected and not trusted. Approve it from a trusted session to continue."
                    elif reason == "mfa_required":
                        msg = "New device requires MFA. Complete multi-factor authentication to continue."
                    elif reason == "mfa_required_risk":
                        msg = "This device was flagged as high risk. Complete MFA to continue or trust it from a known device."
                    elif reason == "monthly_device_quota":
                        msg = "Monthly device limit reached. Remove an old device or wait until next month."
                    elif reason == "yearly_device_quota":
                        msg = "Yearly device limit reached. Remove an old device or wait until next year."
                    elif reason == "user_window_quota":
                        msg = "Device enrollment window exceeded. Remove an old device or contact support."
                    elif reason == "device_quota_exceeded" or reason == "limit_reached":
                        msg = "Maximum devices reached. Remove an old device before signing in from a new one."
                    else:
                        msg = "This device is not allowed to sign in. Contact support."
                    if approval_token and reason in {
                        "untrusted_new_device",
                        "mfa_required",
                        "mfa_required_risk",
                    }:
                        messages.error(self.request, msg)
                        return redirect("users:device_approval_needed")
                    if reason in {
                        "device_quota_exceeded",
                        "limit_reached",
                        "user_window_quota",
                        "monthly_device_quota",
                        "yearly_device_quota",
                    }:
                        try:
                            self.request.session["pending_device_reason"] = reason
                            attempt_id = (exc.context or {}).get(
                                "os_fingerprint"
                            ) or getattr(device_obj, "os_fingerprint", None)
                            if attempt_id:
                                self.request.session["pending_device_attempt"] = (
                                    attempt_id
                                )
                        except Exception:  # noqa: S110
                            pass
                        messages.error(self.request, msg)
                        return redirect("users:device_eviction")
                    if reason == "fallback_identity_blocked":
                        messages.error(
                            self.request,
                            "Device identity was fallback-based and blocked. Provide a device fingerprint to continue.",
                        )
                        return redirect("users:devices")
                    form.add_error(None, msg)
                    return self.form_invalid(form)
            else:
                # Fallback: register device even if AppService is disabled
                try:
                    from apps.devices.services import resolve_or_create_device

                    device, is_new, ctx = resolve_or_create_device(
                        self.request, self.request.user, service_name="login"
                    )
                    enforcement_ctx = ctx or {}
                    self.request.device = device  # type: ignore[attr-defined]
                    try:
                        attach_device_cookie(response, device)  # type: ignore[arg-type]
                    except Exception:
                        logger.debug("attach_device_cookie failed", exc_info=True)
                    if is_new:
                        remaining = enforcement_ctx.get("remaining_devices")
                        reset_days = enforcement_ctx.get("quota_reset_days")
                        suffix = ""
                        if remaining is not None:
                            suffix += f" Remaining device slots: {remaining}."
                        if reset_days is not None:
                            suffix += f" Quotas reset in ~{reset_days} days."
                        messages.info(
                            self.request,
                            f"New device detected and registered.{suffix} Trust it from your account to avoid future prompts.",
                        )
                except Exception:
                    logger.debug("Device registration fallback skipped", exc_info=True)
        except Exception:
            logger.debug("Device policy enforcement skipped", exc_info=True)

        # --- Session fixation protection ---
        try:
            if hasattr(self.request, "session"):
                self.request.session.cycle_key()
                self.request.session.set_expiry(1209600)
        except Exception:
            logger.exception("Failed to rotate session after login")

        user = self.request.user

        try:
            require_mfa = settings_obj.get("require_mfa", False)
            email_verification_mode = getattr(
                settings, "ACCOUNT_EMAIL_VERIFICATION", "optional"
            )
            if (
                require_mfa
                and email_verification_mode == "mandatory"
                and not getattr(user, "email_verified_at", None)
            ):
                logger.info(
                    "Redirecting %s to email verification (MFA required)",
                    getattr(user, "email", user.pk),
                )
                return redirect("users:verify_email")
        except Exception:
            logger.exception("MFA check failed (non-fatal)")

        # --- Friendly prompt to trust new devices ---
        try:
            if enforcement_ctx:
                device = enforcement_ctx.get("device")
                is_new = bool(enforcement_ctx.get("is_new"))
                if device and (is_new or not getattr(device, "is_trusted", False)):
                    # Generate an approval token even when strict mode is off, so the user can trust immediately.
                    try:
                        approval_token = make_device_token(
                            self.request.user.pk,
                            getattr(device, "id", None),
                            "new_device",
                        )
                        self.request.session["pending_device_token"] = approval_token
                        self.request.session["pending_device_reason"] = "new_device"
                    except Exception:  # noqa: S110
                        pass
                    messages.info(
                        self.request,
                        "New device detected. Trust it from your account to avoid future prompts.",
                    )
        except Exception:
            logger.debug("Trust reminder skipped", exc_info=True)

        return response


# ============================================================
# Enterprise Signup View
# ============================================================
class EnterpriseSignupView(SignupView):
    """Tenant-aware signup with optional reCAPTCHA verification."""

    form_class = SignupForm
    template_name = "account/signup.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Import country detection utilities
        from django_countries import countries

        from apps.users.services import (
            COUNTRY_PHONE_CODES,
            detect_country_from_ip,
            get_client_ip,
            get_phone_code_for_country,
        )

        # Detect country from IP
        ip = get_client_ip(self.request)
        detected_country = detect_country_from_ip(ip) if ip else None
        detected_phone_code = (
            get_phone_code_for_country(detected_country) if detected_country else None
        )

        # Build country choices list
        country_choices = list(countries)

        # Build phone code choices list
        phone_code_choices = sorted(
            [(code, dial) for code, dial in COUNTRY_PHONE_CODES.items()],
            key=lambda x: x[0],
        )

        context.update(
            {
                "countries": country_choices,
                "phone_codes": phone_code_choices,
                "detected_country": detected_country,
                "detected_phone_code": detected_phone_code,
                "session_referral_code": self.request.session.get("referral_code", ""),
            }
        )

        return context

    def form_valid(self, form):
        s = _get_settings(self.request)

        if not s.get("enable_signup", True):
            form.add_error(None, "Signup is currently disabled.")
            logger.info("Signup attempt blocked by settings.")
            return self.form_invalid(form)

        token = self.request.POST.get("g-recaptcha-response") or self.request.POST.get(
            "recaptcha_token"
        )
        if s.get("recaptcha_enabled", False):
            if not token:
                form.add_error(None, "reCAPTCHA verification is required.")
                logger.info(
                    "Signup blocked: missing reCAPTCHA token for ip=%s",
                    self.request.META.get("REMOTE_ADDR"),
                )
                return self.form_invalid(form)
            try:
                client_ip = (
                    (
                        self.request.META.get("HTTP_X_FORWARDED_FOR")
                        or self.request.META.get("REMOTE_ADDR")
                        or "unknown"
                    )
                    .split(",")[0]
                    .strip()
                )
                rc = verify_recaptcha(token, client_ip, action="signup")
                if not rc.get("ok"):
                    form.add_error(None, "reCAPTCHA failed. Please retry.")
                    logger.info(
                        "reCAPTCHA failed during signup | ip=%s | rc=%s", client_ip, rc
                    )
                    return self.form_invalid(form)
            except Exception:
                logger.exception("reCAPTCHA error during signup")
                form.add_error(None, "reCAPTCHA error. Please try again.")
                return self.form_invalid(form)

        return super().form_valid(form)


# ============================================================
# Manual email verification (MFA / email)
# ============================================================
@login_required
def verify_email_view(request: HttpRequest) -> HttpResponse:
    """Manual verification for MFA / email confirmation."""
    user: CustomUser = request.user  # type: ignore[assignment]
    # If already verified, skip the page and continue to dashboard
    if getattr(user, "email_verified_at", None):
        return redirect("users:dashboard")

    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        if not code:
            messages.error(request, "Verification code required.")
            return render(request, "users/verify_email.html")

        attempt_key = f"verify_email_attempts:{user.pk}"
        attempts = cache.get(attempt_key, 0)
        if attempts >= 5:
            messages.error(request, "Too many attempts. Please try again later.")
            return render(request, "users/verify_email.html")

        expected = getattr(user, "verification_code", "") or ""
        sent_at = getattr(user, "verification_code_sent_at", None)
        ttl_hours = getattr(settings, "EMAIL_VERIFICATION_CODE_TTL_HOURS", 24)
        expired = not sent_at or timezone.now() - sent_at > timedelta(hours=ttl_hours)

        if not expected or expired:
            messages.error(
                request, "Your verification code has expired. Request a new code."
            )
            return render(request, "users/verify_email.html")

        if code == expected:
            user.email_verified_at = timezone.now()
            user.verification_code = ""
            user.verification_code_sent_at = None
            user.save(
                update_fields=[
                    "email_verified_at",
                    "verification_code",
                    "verification_code_sent_at",
                ]
            )
            cache.delete(attempt_key)
            try:
                from apps.users.services.notifications import send_notification

                send_notification(
                    recipient=user,
                    title="Email verified",
                    message="Your email address has been verified successfully.",
                    level="info",
                )
            except Exception:
                logger.debug("verify_email notification skipped", exc_info=True)
            messages.success(request, "Email verified successfully.")
            return redirect("users:dashboard")

        cache.set(attempt_key, attempts + 1, 900)
        messages.error(request, "Invalid verification code.")
        logger.warning("Invalid verification attempt for user=%s", user.pk)

    return render(request, "users/verify_email.html")


@login_required
@require_http_methods(["GET"])
def verify_email_status(request: HttpRequest) -> JsonResponse:
    """
    Lightweight status endpoint so the client can auto-redirect once
    verification is completed elsewhere (e.g., staff/admin update).
    """
    verified = bool(getattr(request.user, "email_verified_at", None))
    return JsonResponse(
        {
            "verified": verified,
            "redirect": reverse("users:dashboard") if verified else None,
        }
    )


# ============================================================
# Email verification required - gate page
# ============================================================
@login_required
def verify_email_required(request: HttpRequest) -> HttpResponse:
    """
    Gate page shown when a user tries to perform a sensitive action
    but hasn't verified their email yet.

    Social login users should never see this page (they're pre-verified).
    """
    user = request.user

    # Social login users are already verified
    if getattr(user, "signup_method", None) == "social":
        next_url = request.session.pop("next_after_verify", None)
        return redirect(next_url or "users:dashboard")

    # If already verified, redirect to intended destination
    if getattr(user, "email_verified_at", None):
        next_url = request.session.pop("next_after_verify", None)
        return redirect(next_url or "users:dashboard")

    return render(request, "users/verify_email_required.html", {"user": user})


# ============================================================
# Device approval / eviction helpers
# ============================================================
def _get_pending_device_token(request: HttpRequest) -> str | None:
    return request.session.get("pending_device_token")


def device_approval_needed(request: HttpRequest) -> HttpResponse:
    token = _get_pending_device_token(request)
    reason = request.session.get("pending_device_reason", "")
    approval_url = reverse("users:approve_device")
    if token:
        approval_url = f"{approval_url}?t={token}"
    return render(
        request,
        "users/device_approval_needed.html",
        {"token": token, "reason": reason, "approval_url": approval_url},
    )


@login_required
def approve_device(request: HttpRequest) -> HttpResponse:
    token = request.GET.get("t") or _get_pending_device_token(request)
    if not token:
        messages.error(request, "No approval token found.")
        return redirect("users:dashboard")
    data = load_device_token(token)
    if not data:
        messages.error(request, "Approval link is invalid or expired.")
        return redirect("users:dashboard")
    if str(request.user.pk) != str(data.get("u")):
        messages.error(request, "This approval link belongs to another account.")
        return redirect("users:dashboard")
    device_id = data.get("d")
    try:
        ok = mark_device_trusted(device_id, request.user.pk)
        if not ok:
            messages.error(request, "Device not found.")
            return redirect("users:dashboard")
        messages.success(
            request, "Device approved and trusted. You can sign in from it now."
        )
        request.session.pop("pending_device_token", None)
        request.session.pop("pending_device_reason", None)
    except Exception:
        messages.error(request, "Could not approve device. Try again.")
    return redirect("users:devices")


@login_required
@require_http_methods(["GET", "POST"])
def device_eviction(request: HttpRequest) -> HttpResponse:
    """
    Allow users to evict old devices when quota is hit.
    """
    message = ""
    error = ""
    devices = []
    try:
        from apps.devices.models import Device

        if request.method == "POST":
            device_id = request.POST.get("device_id")
            if device_id:
                removed = Device.objects.filter(
                    user=request.user, id=device_id
                ).delete()[0]
                if removed:
                    message = "Device removed. You can now retry from your new device."
                else:
                    error = "Device not found."
        devices = list(
            Device.objects.filter(user=request.user)
            .order_by("last_seen_at")
            .values(
                "id",
                "display_name",
                "os_fingerprint",
                "last_seen_at",
                "is_trusted",
                "is_blocked",
            )
        )
    except Exception as exc:
        error = f"Could not load devices: {exc}"

    return render(
        request,
        "users/device_eviction.html",
        {"devices": devices, "message": message, "error": error},
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_mfa_challenge(request: HttpRequest) -> HttpResponse:
    """
    MFA challenge for device trust. Fails closed until a real MFA provider is wired.
    """
    token = request.GET.get("t") or _get_pending_device_token(request)
    if not token:
        messages.error(request, "No MFA challenge pending.")
        return redirect("users:dashboard")
    data = load_device_token(token)
    if not data or str(request.user.pk) != str(data.get("u")):
        messages.error(request, "This challenge link is invalid or expired.")
        return redirect("users:dashboard")
    attempt_key = f"device_mfa_attempts:{request.user.pk}:{token}"
    attempts = cache.get(attempt_key, 0)
    if attempts >= 5:
        messages.error(request, "Too many attempts. Please try again later.")
        return redirect("users:dashboard")
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()
        if not code:
            messages.error(request, "Enter the code to continue.")
        else:
            cache.set(attempt_key, attempts + 1, 900)
            messages.error(
                request,
                "MFA verification is not available. Contact support to complete device approval.",
            )
            return redirect("users:device_approval_needed")
    return render(
        request,
        "users/device_mfa_challenge.html",
    )


# ============================================================
# Dashboard view
# ============================================================
@login_required(login_url="account_login")
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """Render user dashboard with recent announcements and notifications."""
    s = _get_settings(request)
    # Gate unverified manual users if required
    try:
        if getattr(request.user, "manual_signup", False) and not getattr(
            request.user, "email_verified_at", None
        ):
            return redirect("users:verify_email")
    except Exception:  # noqa: S110
        pass
    now = timezone.now()

    # Announcements: use 'message' (model uses message field)
    announcements = (
        Announcement.objects.filter(start_at__lte=now)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .only("title", "message", "start_at", "expires_at")
        .order_by("-start_at")
    )

    notifications = (
        Notification.objects.filter(recipient=request.user)
        .select_related("recipient")
        # Include recipient to avoid deferred+select_related conflict with .only()
        .only("title", "message", "created_at", "recipient")
        .order_by("-created_at")[:5]
    )

    # Resolve current device (best effort) for trust reminder
    current_device = None
    try:
        from apps.devices.models import Device
        from apps.devices.services import resolve_identity

        ident = resolve_identity(request, user=request.user, service_name="login")
        candidate_id = ident.get("os_fingerprint") or ident.get("server_fallback_fp")
        if candidate_id:
            current_device = (
                Device.objects.filter(user=request.user, os_fingerprint=candidate_id)
                .values(
                    "id", "is_trusted", "is_blocked", "display_name", "last_seen_at"
                )
                .first()
            )
    except Exception:
        current_device = None

    def _display_name(u):
        try:
            full = (getattr(u, "full_name", "") or "").strip()
            if full:
                return full
            username = (getattr(u, "username", "") or "").strip()
            if username:
                return username
            email = (getattr(u, "email", "") or "").strip()
            if email and "@" in email:
                return email.split("@", 1)[0]
        except Exception:  # noqa: S110
            pass
        return "user"

    def _missing_profile_fields(u):
        field_labels = {
            "full_name": "Full name",
            "username": "Username",
            "email": "Email",
            "country": "Country",
            "city": "City",
            "phone": "Phone number",
            "date_of_birth": "Date of birth",
        }
        required = s.get("required_profile_fields") or [
            "full_name",
            "username",
            "email",
        ]
        missing: list[str] = []
        for field in required:  # type: ignore[union-attr]
            label = field_labels.get(field, field.replace("_", " ").title())
            try:
                val = getattr(u, field, None)
                if not (val and str(val).strip()):
                    missing.append(label or field)  # type: ignore[arg-type]
            except Exception:
                missing.append(label or field)  # type: ignore[arg-type]
        return missing

    # Calculate stats
    posts_count = 0
    comments_count = 0
    try:
        from apps.blog.models import Post

        posts_count = Post.objects.filter(author=request.user).count()
    except Exception:  # noqa: S110
        pass

    try:
        from apps.comments.models import Comment

        comments_count = Comment.objects.filter(user=request.user).count()
    except Exception:  # noqa: S110
        pass

    unread_count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()

    # Wallet info
    wallet_balance = 0
    wallet_locked = 0
    wallet_currency = "CREDIT"
    try:
        from apps.wallet.models import Wallet

        wallet = Wallet.objects.filter(user=request.user).first()
        if wallet:
            wallet_balance = wallet.balance
            wallet_locked = wallet.locked_balance
            wallet_currency = wallet.currency
    except Exception:  # noqa: S110
        pass

    profile_completed = getattr(request.user, "profile_completed", False)

    # Referral code for sharing
    user_referral_code = ""
    if profile_completed:
        try:
            from apps.referral.models import ReferralCode

            rc = ReferralCode.objects.filter(user=request.user, is_active=True).first()
            if rc:
                user_referral_code = rc.code
        except Exception:  # noqa: S110
            pass

    # Gamification data for dashboard
    gamification_data: dict[str, Any] = {}
    dash_uid = cast(
        int, request.user.pk
    )  # @login_required guarantees authenticated user
    try:
        from apps.gamification.services import get_current_level, get_total_xp

        total_xp = get_total_xp(dash_uid)
        current_level = get_current_level(dash_uid)
        gamification_data["total_xp"] = total_xp
        gamification_data["level"] = current_level
    except Exception:  # noqa: S110
        pass
    try:
        from apps.gamification.models import Streak, UserBadge

        recent_badges = (
            UserBadge.objects.filter(user=request.user)
            .select_related("badge")
            .order_by("-earned_at")[:5]
        )
        gamification_data["recent_badges"] = recent_badges
        gamification_data["badge_count"] = UserBadge.objects.filter(
            user=request.user
        ).count()
        streak = Streak.objects.filter(user=request.user).first()
        gamification_data["streak"] = streak
    except Exception:  # noqa: S110
        pass

    # Bounty stats for dashboard
    bounty_stats: dict[str, int] = {"created": 0, "fulfilled": 0}
    try:
        from apps.bounty.models import BountyRequest, BountySubmission

        bounty_stats["created"] = BountyRequest.objects.filter(
            user=request.user
        ).count()
        bounty_stats["fulfilled"] = BountySubmission.objects.filter(
            user=request.user, status="accepted"
        ).count()
    except Exception:  # noqa: S110
        pass

    # Download stats
    download_count = 0
    try:
        from apps.firmwares.models import DownloadSession

        download_count = DownloadSession.objects.filter(
            user=request.user, status="completed"
        ).count()
    except Exception:  # noqa: S110
        pass

    context = {
        "site_settings": s,
        "announcements": announcements,
        "notifications": notifications,
        "unread_notifications": unread_count,
        "display_name": _display_name(request.user),
        "profile_missing_fields": _missing_profile_fields(request.user),
        "profile_completed": profile_completed,
        "unlocked_features": _get_platform_features() if profile_completed else [],
        "user_referral_code": user_referral_code,
        "credits": getattr(request.user, "credits", 0),
        "wallet_balance": wallet_balance,
        "wallet_locked": wallet_locked,
        "wallet_currency": wallet_currency,
        "can_watch_ad": bool(s.get("recaptcha_enabled", False)),
        "can_pay": bool(s.get("enable_payments", True)),
        "current_device": current_device,
        "gamification": gamification_data,
        "bounty_stats": bounty_stats,
        "download_count": download_count,
        "stats": {
            "posts_count": posts_count,
            "comments_count": comments_count,
        },
    }
    return render(request, "users/dashboard.html", context)


# ============================================================
# Profile view
# ============================================================
@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    """Render the user profile overview page with inline updates."""
    from django_countries import countries

    from apps.user_profile.models import Profile
    from apps.users.services import (
        COUNTRY_PHONE_CODES,
        auto_detect_user_country,
        get_phone_code_for_country,
    )

    s = _get_settings(request)
    user: CustomUser = request.user  # type: ignore[assignment]

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_full_name":
            full_name = (request.POST.get("full_name") or "").strip()
            user.full_name = full_name
            user.save(update_fields=["full_name"])
            messages.success(request, _("Full name updated."))
        elif action == "update_profile":
            # Inline editable profile fields
            update_fields: list[str] = []
            full_name = (request.POST.get("full_name") or "").strip()
            if full_name != user.full_name:
                user.full_name = full_name
                update_fields.append("full_name")
            phone = (request.POST.get("phone") or "").strip()
            phone_code = (request.POST.get("phone_country_code") or "").strip()
            if phone != (user.phone or ""):
                user.phone = phone or None
                update_fields.append("phone")
            if phone_code != (user.phone_country_code or ""):
                user.phone_country_code = phone_code
                update_fields.append("phone_country_code")
            country = (request.POST.get("country") or "").strip().upper()
            if country and country != (user.country or ""):
                user.country = country
                update_fields.append("country")
            if update_fields:
                user.save(update_fields=update_fields)

            # Profile model fields
            bio = (request.POST.get("bio") or "").strip()
            website = (request.POST.get("website") or "").strip()
            location = (request.POST.get("location") or "").strip()
            Profile.objects.update_or_create(
                user=user,
                defaults={"bio": bio, "website": website, "location": location},
            )
            messages.success(request, _("Profile updated successfully."))
        elif action == "change_password":
            from django.contrib.auth.password_validation import validate_password

            current_pw = request.POST.get("current_password", "")
            new_pw1 = request.POST.get("new_password1", "")
            new_pw2 = request.POST.get("new_password2", "")
            if not user.check_password(current_pw):
                messages.error(request, _("Current password is incorrect."))
            elif new_pw1 != new_pw2:
                messages.error(request, _("New passwords do not match."))
            elif not new_pw1:
                messages.error(request, _("New password cannot be empty."))
            else:
                try:
                    validate_password(new_pw1, user)
                    user.set_password(new_pw1)
                    user.save(update_fields=["password"])
                    update_session_auth_hash(request, user)
                    messages.success(request, _("Password changed successfully."))
                except ValidationError as e:
                    messages.error(request, e.messages[0])

    # Wallet info
    profile_wallet_balance = 0
    profile_wallet_locked = 0
    try:
        from apps.wallet.models import Wallet

        wallet = Wallet.objects.filter(user=user).first()
        if wallet:
            profile_wallet_balance = wallet.balance
            profile_wallet_locked = wallet.locked_balance
    except Exception:  # noqa: S110
        pass

    profile_completed = getattr(user, "profile_completed", False)

    # Load extended profile
    profile_obj = Profile.objects.filter(user=user).first()

    # Auto-detect country from IP if not set
    if not user.country:
        auto_detect_user_country(user, request)

    # Country / phone code detection
    detected_country = user.country or ""
    detected_phone_code = ""
    country_name = ""
    if user.country:
        detected_phone_code = get_phone_code_for_country(user.country)
        country_name = str(dict(countries).get(user.country, user.country))
    if user.phone_country_code:
        detected_phone_code = user.phone_country_code

    # Build country/phone choices for the edit form
    country_choices = list(countries)
    phone_code_choices = sorted(
        [(code, dial) for code, dial in COUNTRY_PHONE_CODES.items()],
        key=lambda x: x[0],
    )

    # Referral code for sharing
    user_referral_code = ""
    referral_stats: dict[str, int] = {"clicks": 0, "conversions": 0}
    if profile_completed:
        try:
            from apps.referral.models import ReferralCode

            rc = ReferralCode.objects.filter(user=user, is_active=True).first()
            if rc:
                user_referral_code = rc.code
                referral_stats = {"clicks": rc.clicks, "conversions": rc.conversions}
        except Exception:  # noqa: S110
            pass

    # Firmware download history
    download_sessions: list[Any] = []
    download_stats: dict[str, int] = {"total": 0, "completed": 0, "free": 0, "paid": 0}
    active_subscription = None
    try:
        from apps.firmwares.models import DownloadSession

        sessions_qs = (
            DownloadSession.objects.filter(user=user)
            .select_related(
                "token",
                "token__firmware",
                "token__firmware__brand",
                "token__firmware__model",
            )
            .order_by("-started_at")
        )
        download_stats["total"] = sessions_qs.count()
        download_stats["completed"] = sessions_qs.filter(status="completed").count()
        # Free downloads = ad-gated tokens
        download_stats["free"] = sessions_qs.filter(
            token__ad_gate_required=True, status="completed"
        ).count()
        download_stats["paid"] = download_stats["completed"] - download_stats["free"]
        download_sessions = list(sessions_qs[:20])
    except Exception:  # noqa: S110
        pass

    try:
        from apps.shop.models import Subscription

        active_subscription = (
            Subscription.objects.filter(user=user, status__in=["active", "trialing"])
            .select_related("plan")
            .first()
        )
    except Exception:  # noqa: S110
        pass

    # Gamification data
    gamification_data: dict[str, Any] = {
        "total_xp": 0,
        "level": None,
        "badges": [],
        "badge_count": 0,
        "total_badges": 0,
        "streak": 0,
    }
    try:
        from apps.gamification.models import Badge, UserBadge
        from apps.gamification.services import get_current_level, get_total_xp

        gamification_data["total_xp"] = get_total_xp(user.pk)
        gamification_data["level"] = get_current_level(user.pk)
        user_badges = (
            UserBadge.objects.filter(user=user)
            .select_related("badge")
            .order_by("-earned_at")[:6]
        )
        gamification_data["badges"] = list(user_badges)
        gamification_data["badge_count"] = UserBadge.objects.filter(user=user).count()
        gamification_data["total_badges"] = Badge.objects.filter(is_active=True).count()
    except Exception:  # noqa: S110
        pass

    # Bounty stats
    bounty_stats: dict[str, int] = {"created": 0, "fulfilled": 0}
    try:
        from apps.bounty.models import BountyRequest, BountySubmission

        bounty_stats["created"] = BountyRequest.objects.filter(user=user).count()
        bounty_stats["fulfilled"] = BountySubmission.objects.filter(
            user=user, status="accepted"
        ).count()
    except Exception:  # noqa: S110
        pass

    return render(
        request,
        "users/profile.html",
        {
            "user": user,
            "credits": getattr(user, "credits", 0),
            "wallet_balance": profile_wallet_balance,
            "wallet_locked": profile_wallet_locked,
            "profile_completed": profile_completed,
            "unlocked_features": _get_platform_features() if profile_completed else [],
            "user_referral_code": user_referral_code,
            "referral_stats": referral_stats,
            "profile_obj": profile_obj,
            "country_name": country_name,
            "countries": country_choices,
            "phone_codes": phone_code_choices,
            "detected_country": detected_country,
            "detected_phone_code": detected_phone_code,
            "download_sessions": download_sessions,
            "download_stats": download_stats,
            "active_subscription": active_subscription,
            "gamification": gamification_data,
            "bounty_stats": bounty_stats,
            "site_settings": s,
        },
    )


# ============================================================
# Auth hub
# ============================================================


def auth_hub_view(request: HttpRequest) -> HttpResponse:
    """Landing page for login/signup/social auth selection."""
    return render(request, "account/hub.html")


# ============================================================
# Tell Us About You – OAuth / profile onboarding
# ============================================================
@login_required
@require_http_methods(["GET", "POST"])
def tell_us_about_you(request: HttpRequest) -> HttpResponse:
    """
    Enhanced profile completion view for social login and new signups.
    Collects:
      • Username and full name
      • Country (auto-detected from IP)
      • Phone number with country code
      • Bio, website, location, interests (writes to Profile)
      • Password (for social accounts without password)
    Shows locked features gallery to motivate completion.
    """
    from django_countries import countries

    from apps.user_profile.models import Profile
    from apps.users.services import (
        COUNTRY_PHONE_CODES,
        auto_detect_user_country,
        get_phone_code_for_country,
    )

    user: CustomUser = request.user  # type: ignore[assignment]

    # Allow re-editing even after profile completion
    editing_completed = getattr(user, "profile_completed", False)

    # Auto-detect country from IP on first visit
    detected_country = None
    detected_phone_code = None
    detected_country_name = ""

    if not user.country:
        auto_detect_user_country(user, request)

    if user.country:
        detected_country = user.country
        detected_phone_code = get_phone_code_for_country(user.country)
        # Resolve country name from django-countries
        detected_country_name = str(dict(countries).get(user.country, ""))

    # Determine if we need password fields
    show_password_fields = not user.has_usable_password()

    # Load existing profile if any
    profile_obj = Profile.objects.filter(user=user).first()

    if request.method == "POST":
        form = TellUsAboutYouForm(request.POST, user=user, request=request)

        if form.is_valid():
            update_fields: list[str] = []

            # Username
            username = form.cleaned_data.get("username", "").strip()
            if username and user.username != username:
                user.username = username
                update_fields.append("username")

            # Full name
            full_name = form.cleaned_data.get("full_name", "").strip()
            if full_name:
                user.full_name = full_name
                update_fields.append("full_name")

            # Country
            country = form.cleaned_data.get("country", "").strip()
            if country and hasattr(user, "country"):
                user.country = country
                update_fields.append("country")

            # Phone
            phone = form.cleaned_data.get("phone", "").strip()
            phone_country_code = form.cleaned_data.get("phone_country_code", "").strip()
            if phone and hasattr(user, "phone"):
                user.phone = phone
                update_fields.append("phone")
            if phone_country_code and hasattr(user, "phone_country_code"):
                user.phone_country_code = phone_country_code
                update_fields.append("phone_country_code")

            # Password for social accounts
            password1 = form.cleaned_data.get("password1")
            password2 = form.cleaned_data.get("password2")
            if password1 and password2 and password1 == password2:
                user.set_password(password1)
                update_fields.append("password")
                update_session_auth_hash(request, user)

            # Only mark profile as completed if all minimum fields are filled
            country = form.cleaned_data.get("country", "").strip()
            interests = form.cleaned_data.get("interests") or []
            username = form.cleaned_data.get("username", "").strip()
            full_name = form.cleaned_data.get("full_name", "").strip()
            profile_ready = bool(username and full_name and country and interests)
            if profile_ready and hasattr(user, "profile_completed"):
                user.profile_completed = True
                update_fields.append("profile_completed")

            # Save all user updates
            if update_fields:
                user.save(update_fields=update_fields)

            # Create / update Profile with extended fields
            bio = form.cleaned_data.get("bio", "").strip()
            website = form.cleaned_data.get("website", "").strip()
            location = form.cleaned_data.get("location", "").strip()

            profile_defaults: dict[str, Any] = {
                "bio": bio,
                "website": website,
                "location": location,
                "social_links": {
                    **(profile_obj.social_links if profile_obj else {}),
                    "interests": interests,
                },
            }
            Profile.objects.update_or_create(
                user=user,
                defaults=profile_defaults,
            )

            if profile_ready:
                # Auto-generate a referral code for the user
                try:
                    from apps.referral.services import get_or_create_referral_code

                    get_or_create_referral_code(user)
                except Exception:
                    logger.debug("Referral code generation skipped", exc_info=True)

                messages.success(
                    request,
                    _(
                        "Welcome! Your profile is complete — all features are now unlocked."
                    ),
                )
            else:
                messages.info(
                    request,
                    _(
                        "Progress saved. Please fill all required fields to unlock features."
                    ),
                )
            return redirect("users:dashboard")
        else:
            # Form has errors - will be shown in template
            pass
    else:
        # GET request - initialize form with user data
        initial_data: dict[str, Any] = {
            "username": user.username if user.username != user.email else "",
            "full_name": getattr(user, "full_name", ""),
            "country": detected_country or "",
            "phone_country_code": detected_phone_code or "",
            "phone": getattr(user, "phone", "") or "",
            "bio": profile_obj.bio if profile_obj else "",
            "website": profile_obj.website if profile_obj else "",
            "location": profile_obj.location if profile_obj else "",
            "interests": (
                profile_obj.social_links.get("interests", [])
                if profile_obj and isinstance(profile_obj.social_links, dict)
                else []
            ),
        }
        form = TellUsAboutYouForm(initial=initial_data, user=user, request=request)

    # Build country choices list
    country_choices = list(countries)

    # Build phone code choices list
    phone_code_choices = sorted(
        [(code, dial) for code, dial in COUNTRY_PHONE_CODES.items()], key=lambda x: x[0]
    )

    # ----- Locked features data (from all apps) -----
    locked_features = _get_platform_features()

    # Calculate completion progress
    filled_count = 0
    total_fields = 5  # username, full_name, country, bio, interests
    if user.username and user.username != user.email:
        filled_count += 1
    if user.full_name:
        filled_count += 1
    if user.country:
        filled_count += 1
    if profile_obj and profile_obj.bio:
        filled_count += 1
    if (
        profile_obj
        and isinstance(profile_obj.social_links, dict)
        and profile_obj.social_links.get("interests")
    ):
        filled_count += 1
    completion_percent = int((filled_count / total_fields) * 100) if total_fields else 0

    context: dict[str, Any] = {
        "user": user,
        "form": form,
        "countries": country_choices,
        "phone_codes": phone_code_choices,
        "detected_country": detected_country,
        "detected_country_name": detected_country_name,
        "detected_phone_code": detected_phone_code,
        "show_password_fields": show_password_fields,
        "locked_features": locked_features,
        "completion_percent": completion_percent,
        "total_locked": len(locked_features),
        "editing_completed": editing_completed,
    }
    return render(request, "users/tell_us_about_you.html", context)


# ============================================================
# Resend email verification
# ============================================================
@login_required
@require_POST
def resend_verification(request: HttpRequest) -> JsonResponse:
    from allauth.account.models import EmailAddress
    from allauth.account.utils import (
        send_email_confirmation,  # type: ignore[attr-defined]
    )

    email = getattr(request.user, "email", "")
    try:
        email_obj = EmailAddress.objects.get(user=request.user, email=email)
        if email_obj.verified:
            return JsonResponse({"ok": False, "error": "already_verified"})
        send_email_confirmation(request, request.user, email=email)
        return JsonResponse({"ok": True})
    except EmailAddress.DoesNotExist:
        return JsonResponse({"ok": False, "error": "email_not_found"})
    except Exception as exc:
        logger.exception("resend_verification failed: %s", exc)
        return JsonResponse({"ok": False, "error": "server_error"}, status=500)


# ============================================================
# Change username
# ============================================================
USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


@login_required
@require_POST
def change_username(request: HttpRequest) -> JsonResponse:
    new_username = (request.POST.get("username") or "").strip()
    if not USERNAME_RE.match(new_username):
        return JsonResponse({"ok": False, "error": "invalid_username"}, status=400)

    User = get_user_model()
    # Allow current username
    if (
        new_username.lower() != (getattr(request.user, "username", "") or "").lower()
        and User.objects.filter(username__iexact=new_username).exists()
    ):
        return JsonResponse({"ok": False, "error": "taken"}, status=409)

    # Enforce change limits: max 2 per calendar year
    now = timezone.now()
    user: CustomUser = request.user  # type: ignore[assignment]
    if user.username_last_changed_at and user.username_last_changed_at.year == now.year:
        if (user.username_changes_this_year or 0) >= 2:
            return JsonResponse({"ok": False, "error": "limit_reached"}, status=429)

    try:
        user.username = new_username
        # Reset counters if new year
        if (
            not user.username_last_changed_at
            or user.username_last_changed_at.year != now.year
        ):
            user.username_changes_this_year = 1
        else:
            user.username_changes_this_year = (user.username_changes_this_year or 0) + 1
        user.username_last_changed_at = now
        user.save(
            update_fields=[
                "username",
                "username_changes_this_year",
                "username_last_changed_at",
            ]
        )
        return JsonResponse({"ok": True})
    except Exception as exc:
        logger.exception("change_username failed: %s", exc)
        return JsonResponse({"ok": False, "error": "server_error"}, status=500)


@login_required
@require_POST
def check_username(request: HttpRequest) -> JsonResponse:
    new_username = (request.POST.get("username") or "").strip()
    if not USERNAME_RE.match(new_username):
        return JsonResponse({"ok": False, "error": "invalid"}, status=400)
    User = get_user_model()
    # Allow current username
    if new_username.lower() == (getattr(request.user, "username", "") or "").lower():
        return JsonResponse({"ok": True, "same": True})
    if User.objects.filter(username__iexact=new_username).exists():
        return JsonResponse({"ok": False, "error": "taken"}, status=409)
    # Pre-calculate if limit reached
    now = timezone.now()
    user: CustomUser = request.user  # type: ignore[assignment]
    limit_reached = False
    if user.username_last_changed_at and user.username_last_changed_at.year == now.year:
        limit_reached = (user.username_changes_this_year or 0) >= 2
    return JsonResponse({"ok": True, "limit_reached": limit_reached})


@login_required
@require_http_methods(["GET", "POST"])
def notification_settings(request: HttpRequest) -> HttpResponse:
    """
    User notification preferences management.
    """
    from apps.users.models import NotificationPreferences

    # Get or create preferences
    preferences, _created = NotificationPreferences.objects.get_or_create(
        user=request.user
    )

    if request.method == "POST":
        try:
            # Email preferences
            preferences.email_comments = request.POST.get("email_comments") == "on"
            preferences.email_replies = request.POST.get("email_replies") == "on"
            preferences.email_mentions = request.POST.get("email_mentions") == "on"
            preferences.email_new_posts = request.POST.get("email_new_posts") == "on"
            preferences.email_frequency = request.POST.get("email_frequency", "instant")

            # Web preferences
            preferences.web_comments = request.POST.get("web_comments") == "on"
            preferences.web_awards = request.POST.get("web_awards") == "on"
            preferences.web_moderation = request.POST.get("web_moderation") == "on"
            preferences.web_system = request.POST.get("web_system") == "on"

            # Quiet hours
            preferences.quiet_hours_enabled = (
                request.POST.get("quiet_hours_enabled") == "on"
            )
            if preferences.quiet_hours_enabled:
                preferences.quiet_hours_start = request.POST.get(
                    "quiet_hours_start", "22:00"
                )
                preferences.quiet_hours_end = request.POST.get(
                    "quiet_hours_end", "08:00"
                )

            preferences.save()
            messages.success(request, "Notification preferences saved successfully!")
            return redirect("users:notification_settings")

        except Exception as exc:
            logger.exception("Failed to save notification preferences: %s", exc)
            messages.error(request, "Failed to save preferences. Please try again.")

    context = {
        "preferences": preferences,
        "vapid_public_key": getattr(settings, "VAPID_PUBLIC_KEY", ""),
    }

    return render(request, "users/notification_settings.html", context)


@login_required
@require_POST
def push_subscription(request: HttpRequest) -> JsonResponse:
    """
    Save push notification subscription from service worker.
    """
    import json

    from apps.users.models import PushSubscription

    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint")
        keys = data.get("keys", {})

        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            return JsonResponse({"error": "Invalid subscription data"}, status=400)

        # Get or create subscription
        subscription, created = PushSubscription.objects.update_or_create(
            user=request.user,
            endpoint=endpoint,
            defaults={
                "p256dh": keys["p256dh"],
                "auth": keys["auth"],
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
                "is_active": True,
            },
        )

        # Enable push in preferences
        from apps.users.models import NotificationPreferences

        prefs, _ = NotificationPreferences.objects.get_or_create(user=request.user)
        prefs.push_enabled = True
        prefs.save(update_fields=["push_enabled"])

        return JsonResponse(
            {"ok": True, "created": created, "subscription_id": subscription.pk}
        )

    except Exception as exc:
        logger.exception("Failed to save push subscription: %s", exc)
        return JsonResponse({"error": "Server error"}, status=500)


@login_required
@require_POST
def unsubscribe_push(request: HttpRequest) -> JsonResponse:
    """
    Unsubscribe from push notifications.
    """
    import json

    from apps.users.models import PushSubscription

    try:
        data = json.loads(request.body)
        endpoint = data.get("endpoint")

        if endpoint:
            PushSubscription.objects.filter(
                user=request.user, endpoint=endpoint
            ).update(is_active=False)
        else:
            # Disable all user subscriptions
            PushSubscription.objects.filter(user=request.user).update(is_active=False)

        return JsonResponse({"ok": True})

    except Exception as exc:
        logger.exception("Failed to unsubscribe push: %s", exc)
        return JsonResponse({"error": "Server error"}, status=500)
