"""
apps.users.views
Enterprise-grade user management and authentication views for the platform.

✅ Highlights
-------------
• Environment-backed project settings with per-app user preferences
• Integrated rate limiting + reCAPTCHA verification
• MFA / Email verification enforcement
• Optimized dashboard queries (deferred, select_related)
• Atomic safety and hardened UX
• Fully compatible with Django 5.x and allauth ≥ 0.65
"""

from __future__ import annotations

import logging
import re
import smtplib
from typing import Any

from allauth.account.forms import LoginForm, SignupForm
from allauth.account.models import EmailAddress
from allauth.account.views import LoginView, PasswordResetView, SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from apps.core.app_service import AppService
from apps.core.geo import calling_code_label, country_label
from apps.core.utils.ip import get_client_ip
from apps.devices.services import DevicePolicyError
from apps.site_settings.services import get_site_settings
from apps.users.forms import TellUsAboutYouForm
from apps.users.mfa_views import begin_login_mfa
from apps.users.models import Announcement, Notification
from apps.users.profile_models import Profile, Reputation
from apps.users.request_user import current_user
from apps.users.services.email_verification import (
    consume_email_verification_code,
    get_pending_email_address,
)
from apps.users.services.location_hints import hint_from_browser, hint_from_request
from apps.users.services.mfa import has_active_totp
from apps.users.services.profile_completion import (
    reward_profile_completion,
    safe_next_url,
)
from apps.users.services.rate_limit import (
    CACHE_ERRORS,
    allow_action,
    get_attempt_count,
    reset_rate_limit,
)
from apps.users.services.recaptcha import verify_recaptcha

logger = logging.getLogger(__name__)

# Failures the optional devices contract may raise while checking a sign-in.
_DEVICE_CHECK_ERRORS: tuple[type[Exception], ...] = (
    DatabaseError,
    *CACHE_ERRORS,
    AttributeError,
    LookupError,
    RuntimeError,
    TypeError,
    ValueError,
)


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """
    Display the authenticated user's profile.
    """
    reputation_obj = Reputation.objects.filter(user=current_user(request)).first()

    context: dict[str, Any] = {
        "user": request.user,
        "notifications": Notification.objects.filter(
            recipient=current_user(request)
        ).order_by("-created_at")[:10],
        "announcements": Announcement.objects.filter(is_active=True).order_by(
            "-created_at"
        )[:5],
        "reputation": reputation_obj,
    }
    return render(request, "users/profile.html", context)


@login_required
@require_http_methods(["GET"])
def devices_view(request: HttpRequest) -> HttpResponse:
    """Compatibility route; device registration is owned by the Devices app."""
    return redirect("devices:my_devices")


def password_reset_entry(request: HttpRequest) -> HttpResponse:
    """Serve allauth's reset form only while the admin toggle allows it."""
    if not _get_settings(request).get("enable_password_reset", False):
        messages.error(request, "Password reset is currently unavailable.")
        return redirect("users:account_login")
    return PasswordResetView.as_view()(request)


# ============================================================
# Settings resolver (lazy import to avoid circular deps)
# ============================================================
def _get_settings(request: HttpRequest | None = None) -> dict[str, object]:
    """
    Return primitive project and users settings without exposing model instances.

    Site-wide values (name, reCAPTCHA) come from ``apps.site_settings``; sign-up,
    MFA, login limits and payments belong to ``UsersSettings``.
    """
    from apps.users.models import UsersSettings

    site = get_site_settings()
    site_values: dict[str, object] = {
        "site_name": site.site_name,
        "recaptcha_enabled": site.recaptcha.enabled,
        "recaptcha_mode": site.recaptcha.mode,
        "recaptcha_score_threshold": site.recaptcha.score_threshold,
        "recaptcha_timeout_ms": site.recaptcha.timeout_ms,
    }
    try:
        us = UsersSettings.get_solo()
    except DatabaseError:
        logger.exception("UsersSettings unavailable; using restrictive defaults")
        return {
            **site_values,
            "enable_signup": False,
            "enable_password_reset": False,
            "max_login_attempts": 5,
            "rate_limit_window_seconds": 300,
            "require_mfa": bool(getattr(settings, "REQUIRE_MFA", False)),
            "enable_payments": True,
            "required_profile_fields": ["full_name", "username", "email"],
        }
    return {
        **site_values,
        "enable_signup": bool(us.enable_signup)
        and bool(getattr(settings, "ENABLE_SIGNUP", True)),
        "enable_password_reset": bool(us.enable_password_reset),
        "max_login_attempts": int(us.max_login_attempts or 5),
        "rate_limit_window_seconds": int(us.rate_limit_window_seconds or 300),
        "require_mfa": bool(us.require_mfa),
        "enable_payments": bool(us.enable_payments),
        "required_profile_fields": list(us.required_profile_fields or []),
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
            "app": "Users",
            "url": "/users/profile/",
        },
        {
            "icon": "user-plus",
            "title": "Social Profile & Followers",
            "description": "Create a public profile, follow other members, and build your activity feed.",
            "app": "Users",
            "url": "/users/profile/",
        },
    ]


def device_registration_prompt(
    enforcement_ctx: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Session payload asking the user to register a new, untrusted browser."""
    if not enforcement_ctx:
        return None
    device = enforcement_ctx.get("device")
    if not (
        device
        and not device.is_trusted
        and not device.is_blocked
        and device.revoked_at is None
    ):
        return None
    context = enforcement_ctx.get("context", {})
    active_devices = context.get("current_device_count", 0)
    max_devices = context.get("max_devices", 3)
    return {
        "device_name": device.display_name,
        "os_family": device.os_family,
        "browser_family": device.browser_family,
        "active_devices": active_devices,
        "max_devices": max_devices,
        "can_approve": active_devices < max_devices,
    }


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

    def form_invalid(self, form):
        if form.is_bound and getattr(form, "user", None) is None:
            settings_obj = _get_settings(self.request)
            ip = get_client_ip(self.request) or "unknown"
            key = f"login:{ip}"
            max_attempts = int(str(settings_obj.get("max_login_attempts", 5)))
            window_seconds = int(
                str(settings_obj.get("rate_limit_window_seconds", 300))
            )
            try:
                attempts = get_attempt_count(key, fail_open=False)
                if attempts >= max_attempts:
                    form.add_error(
                        None, "Too many login attempts. Please try again later."
                    )
                elif not allow_action(
                    key, max_attempts, window_seconds, fail_open=False
                ):
                    form.add_error(
                        None, "Sign-in is temporarily unavailable. Try again later."
                    )
            except (*CACHE_ERRORS, DatabaseError):
                logger.exception("Login rate limiter is unavailable")
                form.add_error(
                    None, "Sign-in is temporarily unavailable. Try again later."
                )
        return super().form_invalid(form)

    def form_valid(self, form):
        login_user = getattr(form, "user", None)

        settings_obj = _get_settings(self.request)
        ip = get_client_ip(self.request) or "unknown"
        enforcement_ctx = None

        try:
            attempts = get_attempt_count(f"login:{ip}", fail_open=False)
            if attempts >= int(str(settings_obj.get("max_login_attempts", 5))):
                form.add_error(None, "Too many login attempts. Please try again later.")
                logger.warning("Rate limit exceeded for IP=%s", ip)
                return super().form_invalid(form)
        except (*CACHE_ERRORS, DatabaseError):
            logger.exception("Login rate limiter is unavailable")
            form.add_error(None, "Sign-in is temporarily unavailable. Try again later.")
            return super().form_invalid(form)

        # --- reCAPTCHA: a missing token is a failure, never a bypass ---
        token = self.request.POST.get("g-recaptcha-response") or self.request.POST.get(
            "recaptcha_token"
        )
        if settings_obj.get("recaptcha_enabled", False):
            if not token:
                form.add_error(None, "reCAPTCHA verification is required.")
                return self.form_invalid(form)
            rc_result = verify_recaptcha(token, ip, action="login")
            if not rc_result.get("ok"):
                form.add_error(None, "reCAPTCHA verification failed. Please try again.")
                logger.info("reCAPTCHA failed for %s : %s", ip, rc_result)
                return self.form_invalid(form)

        if (
            login_user is not None
            and not EmailAddress.objects.filter(
                user=login_user,
                email__iexact=login_user.email,
                verified=True,
            ).exists()
        ):
            return super().form_valid(form)

        if login_user is not None:
            try:
                enforce_policy = AppService.export(
                    "devices", "enforce_device_policy_for_login"
                )
                if not callable(enforce_policy):
                    raise TypeError("Device login policy service is unavailable")
                allowed, enforcement_ctx = enforce_policy(self.request, login_user)
                enforcement_ctx = enforcement_ctx or {}
                if not allowed:
                    reason = enforcement_ctx.get("reason", "policy_denied")
                    form.add_error(None, self._device_policy_message(reason))
                    return super().form_invalid(form)
            except _DEVICE_CHECK_ERRORS:
                logger.exception(
                    "Device policy check failed for user %s", login_user.pk
                )
                form.add_error(
                    None,
                    "Device security check could not be completed. Try again later.",
                )
                return super().form_invalid(form)
            except DevicePolicyError as exc:
                policy_reason = str(getattr(exc, "reason", "policy_denied"))
                if policy_reason in {"mfa_required", "mfa_required_risk"}:
                    try:
                        if has_active_totp(login_user):
                            return begin_login_mfa(self.request, login_user)
                    except DatabaseError:
                        logger.exception(
                            "MFA factors could not be loaded for user %s",
                            login_user.pk,
                        )
                        form.add_error(
                            None,
                            "Two-factor verification is temporarily unavailable.",
                        )
                        return super().form_invalid(form)
                    form.add_error(
                        None,
                        "Set up an authenticator on a trusted device before signing in here.",
                    )
                    return super().form_invalid(form)
                form.add_error(None, self._device_policy_message(policy_reason))
                return super().form_invalid(form)

        if login_user is not None:
            try:
                if has_active_totp(login_user):
                    return begin_login_mfa(
                        self.request,
                        login_user,
                        device_prompt=device_registration_prompt(enforcement_ctx),
                    )
            except DatabaseError:
                logger.exception(
                    "MFA factors could not be loaded for user %s",
                    login_user.pk,
                )
                form.add_error(
                    None,
                    "Two-factor verification is temporarily unavailable.",
                )
                return super().form_invalid(form)

        response = super().form_valid(form)
        if getattr(form, "user", None) is not None:
            reset_rate_limit(f"login:{ip}")
        if enforcement_ctx:
            self.request.device = enforcement_ctx.get("device")
            prompt = device_registration_prompt(enforcement_ctx)
            if prompt:
                self.request.session["device_registration_prompt"] = prompt

        # Session rotation and the remember-me lifetime are handled by allauth's
        # login (django.contrib.auth.login cycles the key; "remember" sets expiry).
        user = self.request.user

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
                "Redirecting user %s to email verification (MFA required)", user.pk
            )
            return redirect("users:verify_email")

        return response

    @staticmethod
    def _device_policy_message(reason: str) -> str:
        messages_by_reason = {
            "blocked_device": "This device is blocked. Contact support to unblock it.",
            "device_key_required": "Device fingerprint is required. Allow device fingerprinting and try again.",
            "untrusted_new_device": "This new device has not been approved. Use an existing trusted session to approve it or contact support.",
            "revoked_device": "This browser registration was revoked. Contact an administrator.",
            "mfa_required": "This device requires MFA, but verification is not currently available. Contact support.",
            "mfa_required_risk": "This device requires additional security verification, which is not currently available. Contact support.",
            "monthly_device_quota": "The monthly device limit has been reached. Contact an administrator.",
            "yearly_device_quota": "The yearly device limit has been reached. Contact an administrator.",
            "user_window_quota": "The device enrollment limit has been reached. Contact an administrator.",
            "device_quota_exceeded": "The maximum number of devices has been reached. Contact an administrator.",
            "limit_reached": "The maximum number of devices has been reached. Contact an administrator to reset or revoke a registration.",
            "fallback_identity_blocked": "Device identity could not be verified. Allow device fingerprinting and try again.",
        }
        return messages_by_reason.get(
            reason, "This device is not allowed to sign in. Contact support."
        )


# ============================================================
# Enterprise Signup View
# ============================================================
class EnterpriseSignupView(SignupView):
    """Tenant-aware signup with optional reCAPTCHA verification."""

    form_class = SignupForm
    template_name = "account/signup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["session_referral_code"] = self.request.session.get("referral_code", "")

        return context

    def form_valid(self, form):
        s = _get_settings(self.request)

        if not s.get("enable_signup", True):
            form.add_error(None, "Signup is currently disabled.")
            logger.info("Signup attempt blocked by settings.")
            return self.form_invalid(form)

        client_ip = get_client_ip(self.request) or "unknown"
        try:
            signup_allowed = allow_action(
                f"signup:{client_ip}",
                max_attempts=int(getattr(settings, "SIGNUP_ATTEMPTS_PER_HOUR", 10)),
                window_seconds=3600,
                fail_open=False,
            )
        except CACHE_ERRORS:
            signup_allowed = False
        if not signup_allowed:
            form.add_error(None, "Too many sign-up attempts. Please try again later.")
            return self.form_invalid(form)

        token = self.request.POST.get("g-recaptcha-response") or self.request.POST.get(
            "recaptcha_token"
        )
        if s.get("recaptcha_enabled", False):
            if not token:
                form.add_error(None, "reCAPTCHA verification is required.")
                logger.info(
                    "Signup blocked: missing reCAPTCHA token for ip=%s", client_ip
                )
                return self.form_invalid(form)
            rc = verify_recaptcha(token, client_ip, action="signup")
            if not rc.get("ok"):
                form.add_error(None, "reCAPTCHA failed. Please retry.")
                logger.info(
                    "reCAPTCHA failed during signup | ip=%s | rc=%s", client_ip, rc
                )
                return self.form_invalid(form)

        return super().form_valid(form)


# ============================================================
# Manual email verification (MFA / email)
# ============================================================
@require_http_methods(["GET", "POST"])
def verify_email_view(request: HttpRequest) -> HttpResponse:
    """Verify an email with the one-time code sent alongside its signed link."""
    if request.method == "POST":
        action = request.POST.get("action", "verify")
        if action == "resend":
            email_address = get_pending_email_address(request)
            if email_address is None or email_address.verified:
                messages.error(request, "No pending email verification was found.")
            else:
                resend_key = (
                    f"users:email-verification:resend:{email_address.pk}:"
                    f"{get_client_ip(request) or 'unknown'}"
                )
                if not allow_action(
                    resend_key,
                    max_attempts=3,
                    window_seconds=900,
                    fail_open=False,
                ):
                    messages.error(
                        request,
                        "Sending verification email is temporarily unavailable.",
                    )
                else:
                    try:
                        email_address.send_confirmation(request=request)
                    except (ImproperlyConfigured, OSError, smtplib.SMTPException):
                        logger.exception(
                            "Verification email delivery failed for address_id=%s",
                            email_address.pk,
                        )
                        messages.error(
                            request,
                            "The verification email could not be sent. Try again later.",
                        )
                    else:
                        messages.success(request, "A new verification email was sent.")
            return redirect("users:verify_email")

        if action != "verify":
            return HttpResponse(status=400)

        result, email_address = consume_email_verification_code(
            request, request.POST.get("code", "")
        )
        if result == "verified" and email_address is not None:
            from allauth.account.adapter import get_adapter
            from allauth.account.internal.flows.email_verification import (
                login_on_verification,
            )

            confirmed = get_adapter(request).confirm_email(request, email_address)
            if confirmed or email_address.verified:
                request.session.pop("users_email_verification", None)
                login_response = login_on_verification(request, email_address)
                if login_response is not None:
                    return login_response
                return redirect(
                    get_adapter(request).get_email_verification_redirect_url(
                        email_address
                    )
                )
            result = "verification_failed"

        messages.error(
            request,
            {
                "expired": "Your verification code expired. Request a new email.",
                "rate_limited": "Too many incorrect codes. Request a new email.",
                "already_verified": "This email address is already verified.",
                "verification_failed": "Email verification could not be completed.",
            }.get(result, "The verification code is invalid."),
        )
        return redirect("users:verify_email")

    email_address = get_pending_email_address(request)
    if email_address is not None and email_address.verified:
        request.session.pop("users_email_verification", None)
        if request.user.is_authenticated:
            return redirect("users:dashboard")
        messages.success(
            request, "Your email address is verified. Sign in to continue."
        )
        return redirect(settings.LOGIN_URL)
    return render(
        request,
        "users/verify_email.html",
        {"pending_email": email_address.email if email_address else ""},
    )


@require_http_methods(["GET"])
@never_cache
def verify_email_status(request: HttpRequest) -> HttpResponse:
    """Report whether the visitor's own pending email address is now verified.

    The verification pages poll this every few seconds so the page moves on by
    itself once the address is confirmed in another tab, from the email link, or
    by staff. It only reveals the state of the address held in the caller's own
    session (or the signed-in account), never another account's.
    """
    user = request.user
    verified = False
    if user.is_authenticated:
        verified = bool(getattr(user, "email_verified_at", None))
    else:
        pending = get_pending_email_address(request)
        verified = bool(pending is not None and pending.verified)
    if verified and not user.is_authenticated:
        request.session.pop("users_email_verification", None)
    destination = (
        reverse("users:dashboard")
        if user.is_authenticated
        else resolve_url(settings.LOGIN_URL)
    )
    if request.headers.get("HX-Request"):
        if not verified:
            return HttpResponse(status=204)
        response = HttpResponse(status=204)
        response["HX-Redirect"] = destination
        return response
    return JsonResponse(
        {"verified": verified, "redirect": destination if verified else None}
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
def device_approval_needed(request: HttpRequest) -> HttpResponse:
    return redirect("devices:my_devices")


@login_required
@require_POST
def approve_device(request: HttpRequest) -> HttpResponse:
    messages.info(
        request,
        "Approve a browser profile from the in-app registration prompt on that browser.",
    )
    return redirect("devices:my_devices")


@login_required
@require_http_methods(["GET", "POST"])
def device_eviction(request: HttpRequest) -> HttpResponse:
    """Compatibility route; only administrators may revoke registrations."""
    messages.info(
        request,
        "Users cannot remove registered browsers. Contact an administrator for a reset.",
    )
    return redirect("devices:my_devices")


@login_required
@require_http_methods(["GET", "POST"])
def device_mfa_challenge(request: HttpRequest) -> HttpResponse:
    """Legacy route; sign-in MFA and browser registration are separate workflows."""
    messages.info(
        request,
        "Authenticator verification is separate from registering this browser.",
    )
    return redirect("users:mfa_verify")


# ============================================================
# Dashboard view
# ============================================================
@login_required(login_url="account_login")
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """Render user dashboard with recent announcements and notifications."""
    s = _get_settings(request)
    user = current_user(request)
    # Gate unverified manual users if required
    if user.manual_signup and not user.email_verified_at:
        return redirect("users:verify_email")
    now = timezone.now()

    # Announcements: use 'message' (model uses message field)
    audiences = ["all", "staff"] if user.is_staff else ["all", "user"]
    announcements = (
        Announcement.objects.filter(is_active=True, audience__in=audiences)
        .filter(start_at__lte=now)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .only("title", "message", "start_at", "expires_at", "audience", "is_active")
        .order_by("-start_at")
    )

    notifications = (
        Notification.objects.filter(recipient=user)
        .select_related("recipient")
        # Include recipient to avoid deferred+select_related conflict with .only()
        .only("title", "message", "created_at", "recipient")
        .order_by("-created_at")[:5]
    )

    # Resolve the current browser registration for the trust reminder
    from apps.devices.models import Device
    from apps.devices.services import resolve_identity

    ident = resolve_identity(request, user=user, service_name="login")
    current_device = (
        Device.objects.filter(user=user, browser_key_hash=ident["browser_key_hash"])
        .values("id", "is_trusted", "is_blocked", "display_name", "last_seen_at")
        .first()
    )

    def _display_name(u):
        return (
            (u.full_name or "").strip()
            or (u.username or "").strip()
            or (u.email.split("@", 1)[0] if "@" in (u.email or "") else "user")
        )

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
        raw_required = s.get("required_profile_fields")
        required = (
            [str(f) for f in raw_required]
            if isinstance(raw_required, list) and raw_required
            else ["full_name", "username", "email"]
        )
        missing: list[str] = []
        for field in required:
            label = field_labels.get(field, field.replace("_", " ").title())
            val = getattr(u, field, None)
            if not (val and str(val).strip()):
                missing.append(label or field)
        return missing

    from apps.blog.models import Post
    from apps.comments.models import Comment
    from apps.firmwares.models import DownloadSession

    posts_count = Post.objects.filter(author=user).count()
    comments_count = Comment.objects.filter(user=user).count()

    unread_count = Notification.objects.filter(recipient=user, is_read=False).count()

    profile_completed = user.profile_completed
    download_count = DownloadSession.objects.filter(
        user=user, status="completed"
    ).count()

    context = {
        "announcements": announcements,
        "notifications": notifications,
        "unread_notifications": unread_count,
        "display_name": _display_name(user),
        "profile_missing_fields": _missing_profile_fields(user),
        "profile_completed": profile_completed,
        "unlocked_features": _get_platform_features() if profile_completed else [],
        "can_watch_ad": bool(s.get("recaptcha_enabled", False)),
        "can_pay": bool(s.get("enable_payments", True)),
        "current_device": current_device,
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

    from apps.users.services import get_phone_code_for_country

    user = current_user(request)

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
            if country:
                valid_countries = {code for code, _name in countries}
                if country in valid_countries and country != (user.country or ""):
                    user.country = country
                    update_fields.append("country")
                elif country not in valid_countries:
                    messages.error(request, _("Choose a valid country."))
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

        return redirect("users:profile")

    profile_completed = getattr(user, "profile_completed", False)

    # Load extended profile
    profile_obj = Profile.objects.filter(user=user).first()

    # Nothing is saved until the form is posted; an unsaved country is only a hint.
    hint = None if user.country else hint_from_request(request)
    country_code = user.country or (hint.country if hint else "")
    phone_code = user.phone_country_code or (
        get_phone_code_for_country(country_code) if country_code else ""
    )

    # Firmware download history
    download_sessions: list[Any] = []
    download_stats: dict[str, int] = {"total": 0, "completed": 0, "free": 0, "paid": 0}
    from apps.firmwares.models import DownloadSession

    sessions_qs = (
        DownloadSession.objects.filter(user=user)
        .select_related("token")
        .prefetch_related("token__firmware")
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

    device_summary = None
    get_device_summary = AppService.export("devices", "get_device_registration_summary")
    if callable(get_device_summary):
        try:
            device_summary = get_device_summary(user)
        except _DEVICE_CHECK_ERRORS:
            logger.exception(
                "Device registration summary unavailable for user_id=%s", user.pk
            )

    return render(
        request,
        "users/profile.html",
        {
            "user": user,
            "profile_completed": profile_completed,
            "unlocked_features": _get_platform_features() if profile_completed else [],
            "profile_obj": profile_obj,
            "country_code": country_code,
            "country_display": country_label(country_code),
            "phone_code": phone_code,
            "phone_display": calling_code_label(phone_code, country_code),
            "detected": "true" if hint else "",
            "detected_source": hint.source if hint else "",
            "locale_hint_url": reverse("users:locale_hint"),
            "download_sessions": download_sessions,
            "download_stats": download_stats,
            "device_summary": device_summary,
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
    from apps.users.services import get_phone_code_for_country

    user = current_user(request)
    next_url = safe_next_url(
        request, request.POST.get("next") or request.GET.get("next")
    )

    # Allow re-editing even after profile completion
    editing_completed = getattr(user, "profile_completed", False)

    # Suggest a location only when nothing is saved; a GET never persists it.
    hint = (
        None if user.country or request.method == "POST" else hint_from_request(request)
    )
    detected_country = user.country or (hint.country if hint else "")
    detected_phone_code = user.phone_country_code or (
        get_phone_code_for_country(detected_country) if detected_country else ""
    )
    detected_country_name = country_label(detected_country)
    detected_source = hint.source if hint else ""

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
                reward_profile_completion(user)
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
            return redirect(next_url or "users:dashboard")
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
        form = TellUsAboutYouForm(
            initial=initial_data,
            user=user,
            request=request,
            detected_fields=("country", "phone_country_code") if hint else (),
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
        "detected_country": detected_country if hint else "",
        "detected_country_name": detected_country_name if hint else "",
        "detected_phone_code": detected_phone_code if hint else "",
        "detected_source": detected_source,
        "locale_hint_url": reverse("users:locale_hint"),
        "next_url": next_url,
        "show_password_fields": show_password_fields,
        "locked_features": locked_features,
        "completion_percent": completion_percent,
        "total_locked": len(locked_features),
        "editing_completed": editing_completed,
    }
    return render(request, "users/tell_us_about_you.html", context)


@require_GET
@never_cache
def locale_hint(request: HttpRequest) -> JsonResponse:
    """Resolve the browser's time zone / language region to a country suggestion.

    Pure lookup in the static tables (no IP, no storage). The caller fills only
    empty fields; an unknown zone and language answer ``{"detected": false}``.
    """
    hint = hint_from_browser(request.GET.get("tz"), request.GET.get("lang"))
    if hint is None:
        return JsonResponse({"detected": False})
    return JsonResponse({"detected": True, **hint.as_dict()})


# ============================================================
# Resend email verification
# ============================================================
@login_required
@require_POST
def resend_verification(request: HttpRequest) -> JsonResponse:
    email = getattr(request.user, "email", "")
    try:
        email_obj = EmailAddress.objects.get(user=request.user, email=email)
        if email_obj.verified:
            return JsonResponse({"ok": False, "error": "already_verified"})
        resend_key = f"users:email-verification:resend:{email_obj.pk}"
        if not allow_action(
            resend_key,
            max_attempts=3,
            window_seconds=900,
            fail_open=False,
        ):
            return JsonResponse(
                {"ok": False, "error": "rate_limited"},
                status=429,
            )
        email_obj.send_confirmation(request=request)
        return JsonResponse({"ok": True})
    except EmailAddress.DoesNotExist:
        return JsonResponse({"ok": False, "error": "email_not_found"})
    except (ImproperlyConfigured, OSError, smtplib.SMTPException):
        logger.exception(
            "Verification email delivery failed for user_id=%s",
            request.user.pk,
        )
        return JsonResponse(
            {"ok": False, "error": "email_unavailable"},
            status=503,
        )


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
    user = current_user(request)
    if (
        user.username_last_changed_at
        and user.username_last_changed_at.year == now.year
        and (user.username_changes_this_year or 0) >= 2
    ):
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
    except DatabaseError:
        logger.exception("change_username failed")
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
    user = current_user(request)
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
        user=current_user(request)
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

        except (DatabaseError, ValidationError):
            logger.exception("Failed to save notification preferences")
            messages.error(request, "Failed to save preferences. Please try again.")

    from apps.users.services.notifications import push_delivery_available

    push_available = push_delivery_available()
    context = {
        "preferences": preferences,
        "push_available": push_available,
        "vapid_public_key": getattr(settings, "VAPID_PUBLIC_KEY", "")
        if push_available
        else "",
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
    from apps.users.services.notifications import push_delivery_available

    if not push_delivery_available():
        return JsonResponse(
            {"error": "Push notifications are not enabled on this server."},
            status=503,
        )

    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid subscription data"}, status=400)
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    keys = data.get("keys", {}) if isinstance(data, dict) else {}

    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return JsonResponse({"error": "Invalid subscription data"}, status=400)

    # Get or create subscription
    subscription, created = PushSubscription.objects.update_or_create(
        user=current_user(request),
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

    prefs, _ = NotificationPreferences.objects.get_or_create(user=current_user(request))
    prefs.push_enabled = True
    prefs.save(update_fields=["push_enabled"])

    return JsonResponse(
        {"ok": True, "created": created, "subscription_id": subscription.pk}
    )


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
                user=current_user(request), endpoint=endpoint
            ).update(is_active=False)
        else:
            # Disable all user subscriptions
            PushSubscription.objects.filter(user=current_user(request)).update(
                is_active=False
            )

        return JsonResponse({"ok": True})

    except (ValueError, AttributeError, DatabaseError):
        logger.exception("Failed to unsubscribe push")
        return JsonResponse({"error": "Server error"}, status=500)
