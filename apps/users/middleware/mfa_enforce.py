from __future__ import annotations

import logging
from typing import ClassVar

from allauth.account.models import EmailAddress
from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from apps.users.models import MFADevice, UsersSettings

logger = logging.getLogger(__name__)


class EnforceMfaMiddleware:
    SAFE_URL_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "account_login",
            "account_logout",
            "account_signup",
            "users:account_login",
            "users:account_logout",
            "users:account_signup",
            "users:verify_email",
            "users:verify_email_required",
            "users:tell_us_about_you",
            "users:mfa_setup",
            "users:mfa_verify",
            "users:mfa_backup_codes",
        }
    )
    SAFE_PATH_PREFIXES: ClassVar[tuple[str, ...]] = (
        "/static/",
        "/media/",
        "/health/",
        "/site/",
        "/.well-known/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        if any(
            request.path_info.startswith(prefix) for prefix in self.SAFE_PATH_PREFIXES
        ):
            return self.get_response(request)

        try:
            view_name = resolve(request.path_info).view_name
        except Resolver404:
            view_name = None
        if view_name in self.SAFE_URL_NAMES:
            return self.get_response(request)

        try:
            require_mfa = bool(
                getattr(settings, "REQUIRE_MFA", False)
                or UsersSettings.get_solo().require_mfa
            )
            has_totp = MFADevice.objects.filter(
                user=user,
                device_type="totp",
                is_active=True,
            ).exists()
            email_verified = bool(
                getattr(user, "email_verified_at", None)
                or EmailAddress.objects.filter(user=user, verified=True).exists()
            )
        except DatabaseError:
            logger.exception(
                "Could not load authentication policy for user %s", user.pk
            )
            return HttpResponse(
                "Account security policy is temporarily unavailable.",
                status=503,
            )

        if require_mfa and not email_verified:
            return redirect("users:verify_email")
        if require_mfa and not has_totp:
            return redirect("users:mfa_setup")
        if (require_mfa or has_totp) and request.session.get(
            "mfa_verified_user_id"
        ) != str(user.pk):
            return redirect("users:mfa_verify")
        return self.get_response(request)
