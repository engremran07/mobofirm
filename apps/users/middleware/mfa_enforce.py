from __future__ import annotations

import logging
from collections.abc import Iterable

from django.shortcuts import redirect
from django.urls import resolve, reverse

logger = logging.getLogger(__name__)


class EnforceMfaMiddleware:
    """
    Enforce MFA when UsersSettings.require_mfa is enabled.

    - Skips static/admin/api/consent paths.
    - Applies only to authenticated users.
    - Redirects to MFA setup if user has MFA required but no active device.
    - Redirects to MFA challenge if user has devices but hasn't verified this session.
    """

    SAFE_URL_NAMES: Iterable[str] = {
        "account_login",
        "account_logout",
        "account_signup",
        "users:verify_email",
        "users:mfa_setup",
        "users:mfa_verify",
    }
    SAFE_PATH_PREFIXES: Iterable[str] = (
        "/admin",
        "/static",
        "/media",
        "/api",
        "/consent",
        "/.well-known",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.mfa_setup_url = "users:mfa_setup"
        self.mfa_verify_url = "users:mfa_verify"
        self.verify_email_url = "users:verify_email"

    def __call__(self, request):
        user = getattr(request, "user", None)

        try:
            from apps.users.models import UsersSettings

            us = UsersSettings.get_solo()
            require_mfa = bool(getattr(us, "require_mfa", False))
        except Exception:
            require_mfa = False

        if not require_mfa or not user or not user.is_authenticated:
            return self.get_response(request)

        path = request.path
        for prefix in self.SAFE_PATH_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        try:
            match = request.resolver_match or resolve(path)
            if match and match.view_name in self.SAFE_URL_NAMES:
                return self.get_response(request)
        except Exception:
            return self.get_response(request)

        # Check if user has verified email first
        if require_mfa and not getattr(user, "email_verified_at", None):
            try:
                return redirect(reverse(self.verify_email_url))
            except Exception:
                return self.get_response(request)

        # Check if user has MFA devices enrolled
        try:
            from apps.users.models import MFADevice

            has_active_device = MFADevice.objects.filter(
                user=user, is_active=True, device_type="totp"
            ).exists()

            if not has_active_device:
                # User needs to set up MFA
                try:
                    return redirect(reverse(self.mfa_setup_url))
                except Exception:
                    logger.debug("MFA setup URL not configured yet")
                    return self.get_response(request)

            # Check if MFA has been verified this session
            mfa_verified = request.session.get("mfa_verified", False)
            if not mfa_verified:
                # Redirect to MFA challenge
                try:
                    return redirect(reverse(self.mfa_verify_url))
                except Exception:
                    logger.debug("MFA verify URL not configured yet")
                    return self.get_response(request)

        except ImportError:
            logger.warning("MFADevice model not available")
        except Exception as e:
            logger.exception(f"Error checking MFA status: {e}")

        return self.get_response(request)
