from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.utils.ip import get_client_ip
from apps.users.mfa import TOTPService
from apps.users.models import MFADevice
from apps.users.request_user import current_user
from apps.users.services.mfa import (
    has_active_totp,
    mfa_required_globally,
    verify_mfa_code,
)
from apps.users.services.rate_limit import allow_action, reset_rate_limit

_PENDING_USER = "pending_mfa_user_id"
_PENDING_EXPIRES = "pending_mfa_expires_at"
_PENDING_SETUP_SECRET = "pending_mfa_setup_secret"
_PENDING_SETUP_CREATED = "pending_mfa_setup_created_at"
_BACKUP_CODES_ONCE = "mfa_backup_codes_once"
_MFA_VERIFIED_USER = "mfa_verified_user_id"
_ATTEMPT_LIMIT = 5
_ATTEMPT_WINDOW_SECONDS = 300
_PENDING_LOGIN_SECONDS = 300
_PENDING_SETUP_SECONDS = 600


def _attempt_key(request: HttpRequest, user_id: int) -> str:
    """Key by account and client so a password holder cannot lock out the owner."""
    return f"users:mfa:verify:{user_id}:{get_client_ip(request) or 'unknown'}"


def _render_verify(
    request: HttpRequest,
    user: Any,
    *,
    is_pre_auth: bool,
    is_verified: bool = False,
    status: int = 200,
) -> HttpResponse:
    require_mfa = mfa_required_globally()
    return render(
        request,
        "users/mfa_verify.html",
        {
            "is_pre_auth": is_pre_auth,
            "is_verified": is_verified,
            "can_disable": bool(not is_pre_auth and not require_mfa),
        },
        status=status,
    )


@login_required(login_url="users:account_login")
@require_http_methods(["GET", "POST"])
def mfa_setup(request: HttpRequest) -> HttpResponse:
    if has_active_totp(request.user):
        return redirect("users:mfa_verify")

    secret = request.session.get(_PENDING_SETUP_SECRET)
    created_at = request.session.get(_PENDING_SETUP_CREATED)
    now = timezone.now()
    if (
        not isinstance(secret, str)
        or not isinstance(created_at, (int, float))
        or now.timestamp() - created_at > _PENDING_SETUP_SECONDS
    ):
        secret = TOTPService.generate_secret()
        request.session[_PENDING_SETUP_SECRET] = secret
        request.session[_PENDING_SETUP_CREATED] = now.timestamp()

    context = {
        "secret": secret,
    }

    if request.method == "POST":
        if not allow_action(
            _attempt_key(request, current_user(request).pk),
            max_attempts=_ATTEMPT_LIMIT,
            window_seconds=_ATTEMPT_WINDOW_SECONDS,
            fail_open=False,
        ):
            messages.error(request, "Too many verification attempts. Try again later.")
            return redirect("users:mfa_setup")

        matched_counter = TOTPService.matching_counter(
            secret,
            request.POST.get("code", ""),
        )
        if matched_counter is None:
            messages.error(request, "That authenticator code is not valid.")
            return redirect("users:mfa_setup")

        with transaction.atomic():
            locked_user = (
                get_user_model()
                .objects.select_for_update()
                .get(pk=current_user(request).pk)
            )
            if has_active_totp(locked_user):
                messages.info(request, "An authenticator is already configured.")
                return redirect("users:mfa_verify")

            device = MFADevice(
                user=locked_user,
                device_type="totp",
                name="Authenticator app",
                is_primary=True,
                is_active=False,
            )
            device.set_secret(secret)
            device.is_active = True
            device.last_used_counter = matched_counter
            device.save()

            backup_device = MFADevice.objects.create(
                user=locked_user,
                device_type="backup",
                name="Recovery codes",
                is_primary=False,
                is_active=True,
            )
            backup_codes = backup_device.generate_backup_codes()

        request.session.pop(_PENDING_SETUP_SECRET, None)
        request.session.pop(_PENDING_SETUP_CREATED, None)
        request.session[_MFA_VERIFIED_USER] = str(current_user(request).pk)
        request.session[_BACKUP_CODES_ONCE] = backup_codes
        reset_rate_limit(_attempt_key(request, current_user(request).pk))
        messages.success(request, "Two-factor authentication is enabled.")
        return redirect("users:mfa_backup_codes")

    return render(request, "users/mfa_setup.html", context)


@login_required(login_url="users:account_login")
@require_GET
def mfa_backup_codes(request: HttpRequest) -> HttpResponse:
    codes = request.session.pop(_BACKUP_CODES_ONCE, None)
    if codes is None:
        messages.info(request, "Recovery codes are shown only once.")
        return redirect("users:dashboard")
    return render(request, "users/mfa_backup_codes.html", {"backup_codes": codes})


@require_http_methods(["GET", "POST"])
def mfa_verify(request: HttpRequest) -> HttpResponse:
    pending_user_id = request.session.get(_PENDING_USER)
    pending_expiry = request.session.get(_PENDING_EXPIRES)
    is_pre_auth = pending_user_id is not None

    if pending_user_id is not None:
        if (
            not isinstance(pending_expiry, (int, float))
            or timezone.now().timestamp() > pending_expiry
        ):
            request.session.pop(_PENDING_USER, None)
            request.session.pop(_PENDING_EXPIRES, None)
            messages.error(
                request, "Your sign-in verification expired. Please sign in again."
            )
            return redirect("users:account_login")
        user = get_user_model().objects.filter(pk=pending_user_id).first()
        if user is None or not user.is_active:
            request.session.pop(_PENDING_USER, None)
            request.session.pop(_PENDING_EXPIRES, None)
            messages.error(
                request, "This account could not be verified. Please sign in again."
            )
            return redirect("users:account_login")
    elif request.user.is_authenticated:
        user = request.user
    else:
        return redirect("users:account_login")

    if not has_active_totp(user):
        if is_pre_auth:
            request.session.pop(_PENDING_USER, None)
            request.session.pop(_PENDING_EXPIRES, None)
            messages.error(
                request,
                "No authenticator is configured. Sign in from a trusted device to set one up.",
            )
            return redirect("users:account_login")
        if mfa_required_globally():
            return redirect("users:mfa_setup")
        messages.info(request, "No authenticator is configured for this account.")
        return redirect("users:dashboard")

    already_verified = not is_pre_auth and request.session.get(
        _MFA_VERIFIED_USER
    ) == str(user.pk)
    if request.method == "POST":
        if not allow_action(
            _attempt_key(request, user.pk),
            max_attempts=_ATTEMPT_LIMIT,
            window_seconds=_ATTEMPT_WINDOW_SECONDS,
            fail_open=False,
        ):
            messages.error(request, "Too many verification attempts. Try again later.")
            return redirect("users:mfa_verify")

        if not verify_mfa_code(user, request.POST.get("code", "")):
            messages.error(request, "That authenticator or recovery code is not valid.")
            return redirect("users:mfa_verify")

        reset_rate_limit(_attempt_key(request, user.pk))
        if is_pre_auth:
            login(
                request,
                user,
                backend="allauth.account.auth_backends.AuthenticationBackend",
            )
            request.session.pop(_PENDING_USER, None)
            request.session.pop(_PENDING_EXPIRES, None)

        request.session[_MFA_VERIFIED_USER] = str(user.pk)
        messages.success(request, "Two-factor verification succeeded.")
        return redirect("users:dashboard")

    return _render_verify(
        request,
        user,
        is_pre_auth=is_pre_auth,
        is_verified=already_verified,
    )


@login_required(login_url="users:account_login")
@require_POST
def mfa_disable(request: HttpRequest) -> HttpResponse:
    if mfa_required_globally():
        return HttpResponse(
            "Two-factor authentication is required by your administrator.",
            status=403,
        )

    if not allow_action(
        _attempt_key(request, current_user(request).pk),
        max_attempts=_ATTEMPT_LIMIT,
        window_seconds=_ATTEMPT_WINDOW_SECONDS,
        fail_open=False,
    ):
        messages.error(request, "Too many verification attempts. Try again later.")
        return redirect("users:mfa_verify")

    code = request.POST.get("code", "")
    if not verify_mfa_code(request.user, code):
        messages.error(request, "A valid authenticator or recovery code is required.")
        return redirect("users:mfa_verify")

    MFADevice.objects.filter(user=current_user(request)).delete()
    request.session.pop(_MFA_VERIFIED_USER, None)
    reset_rate_limit(_attempt_key(request, current_user(request).pk))
    messages.success(request, "Two-factor authentication has been disabled.")
    return redirect("users:dashboard")


def begin_login_mfa(request: HttpRequest, user: Any) -> HttpResponse:
    request.session[_PENDING_USER] = user.pk
    request.session[_PENDING_EXPIRES] = (
        timezone.now() + timedelta(seconds=_PENDING_LOGIN_SECONDS)
    ).timestamp()
    return redirect("users:mfa_verify")
