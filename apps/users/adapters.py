"""
Enterprise-grade Account & Social Adapters for the platform.
Integrates OAuth onboarding, trusted social email verification, and safe redirects.
"""

from __future__ import annotations

import logging
from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.consent.utils import hash_ip
from apps.core.utils.ip import get_client_ip

logger = logging.getLogger(__name__)


def _safe_reverse(name: str, default: str = "/") -> str:
    """Best-effort reverse that never raises; used for login/onboarding redirects."""
    try:
        return reverse(name)
    except NoReverseMatch:
        logger.warning("reverse(%s) failed - fallback=%s", name, default)
        return default
    except Exception as exc:
        logger.exception("reverse(%s) unexpected error: %s", name, exc)
        return default


class CustomAccountAdapter(DefaultAccountAdapter):
    """Account adapter with hardened password rules and trusted social email."""

    def send_mail(self, template_prefix, email, context):
        # Enrich reset/verification emails with timestamp and anonymized requester IP when available.
        context = context or {}
        context.setdefault("now", timezone.now())
        try:
            request = context.get("request")
            ip = get_client_ip(request) if request else None
            if ip:
                context["client_ip"] = hash_ip(ip)
        except Exception:
            context.setdefault("client_ip", None)
        return super().send_mail(template_prefix, email, context)

    def is_open_for_signup(self, request: HttpRequest | None) -> bool:
        try:
            from apps.site_settings.models import SiteSettings  # type: ignore

            settings_obj = SiteSettings.get_solo()
            return bool(getattr(settings_obj, "enable_signup", True))
        except Exception as exc:
            logger.warning("Signup availability check failed: %s", exc)
            return True

    def clean_password(self, password: str, user: Any | None = None) -> str:
        if not isinstance(password, str):
            raise ValidationError(_("Invalid password format."))
        if len(password) < 8:
            raise ValidationError(_("Password must be at least 8 characters long."))
        if password.isdigit():
            raise ValidationError(_("Password cannot be entirely numeric."))
        return super().clean_password(password, user)

    def is_email_verified(self, user):
        try:
            social = getattr(user, "socialaccount_set", None)
            if social and social.first():
                return True
        except Exception:  # noqa: S110
            pass
        return super().is_email_verified(user)  # type: ignore[call-arg]

    def get_login_redirect_url(self, request: HttpRequest) -> str:
        try:
            user = getattr(request, "user", None)
            if user and getattr(user, "email_verified_at", None) is None:
                verification_required = False
                if getattr(user, "manual_signup", False):
                    verification_required = True
                elif (
                    getattr(settings, "ACCOUNT_EMAIL_VERIFICATION", "optional")
                    == "mandatory"
                ):
                    verification_required = True

                if verification_required:
                    try:
                        messages.info(
                            request, _("Please verify your email to continue.")
                        )
                    except Exception:  # noqa: S110
                        pass
                    return _safe_reverse("users:verify_email", default="/")
        except Exception as exc:
            logger.exception("Login redirect evaluation failed: %s", exc)
        return _safe_reverse("users:dashboard", default="/")

    def get_signup_redirect_url(self, request: HttpRequest) -> str:
        """Redirect email signups - show profile prompt if incomplete"""
        user = getattr(request, "user", None)
        if user and not getattr(user, "profile_completed", False):
            return _safe_reverse("users:tell_us_about_you", default="/users/profile/")
        return _safe_reverse("users:dashboard", default="/")

    def get_email_verification_redirect_url(self, email_address) -> str:
        """After email verification, redirect to profile completion if needed"""
        user = getattr(email_address, "user", None)
        if user and not getattr(user, "profile_completed", False):
            return _safe_reverse("users:tell_us_about_you", default="/users/profile/")
        return _safe_reverse("users:dashboard", default="/")


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Social adapter that trusts provider email and defers completion to onboarding."""

    def get_connect_redirect_url(self, request: HttpRequest, socialaccount) -> str:
        logger.debug(
            "Social connect redirect (provider=%s)",
            getattr(socialaccount, "provider", None),
        )
        return _safe_reverse("users:tell_us_about_you", default="/users/profile/")

    def get_signup_redirect_url(self, request: HttpRequest) -> str:
        """Redirect social signups to onboarding if profile not completed"""
        user = getattr(request, "user", None)
        if user and not getattr(user, "profile_completed", False):
            logger.debug("Social signup redirect -> users:tell_us_about_you")
            return _safe_reverse("users:tell_us_about_you", default="/users/profile/")
        logger.debug(
            "Social signup redirect -> users:dashboard (profile already complete)"
        )
        return _safe_reverse("users:dashboard", default="/")

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin) -> None:
        """
        Keep minimal: if the user is already fully onboarded, do nothing.
        Otherwise, let EnforceProfileCompletionMiddleware drive them to the
        tell-us-about-you flow.
        """
        try:
            user = getattr(sociallogin, "user", None)
            if (
                user
                and getattr(user, "id", None)
                and getattr(user, "profile_completed", False)
            ):
                sociallogin.redirect_url = _safe_reverse("users:dashboard", default="/")  # type: ignore[attr-defined]
                return
            logger.debug("pre_social_login: social login requires onboarding.")
        except Exception as exc:
            logger.exception("pre_social_login fatal error: %s", exc)
            return
