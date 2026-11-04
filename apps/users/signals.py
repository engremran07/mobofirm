"""
apps.users.signals
Centralized user-related signal handlers for the platform.
"""

from __future__ import annotations

import logging

from allauth.account.signals import email_confirmed, user_signed_up

try:
    from allauth.account.signals import password_changed, password_set
except Exception:  # allauth version guard
    password_changed = None
    password_set = None
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from apps.core.utils.ip import get_client_ip
from apps.users.services.notifications import send_notification

logger = logging.getLogger(__name__)


@receiver(user_signed_up, dispatch_uid="users_handle_signed_up_v1")
def handle_user_signed_up(request, user, **kwargs):
    """
    Triggered immediately after a new user account is created
    (via email, social, or SSO signup).

    Responsibilities:
      • Set signup_method ("manual" | "social").
      • Ensure profile_completed=False so middleware can enforce onboarding.
      • Synchronize provider-verified social email state.
    """
    if not user:
        logger.debug("handle_user_signed_up: missing user instance.")
        return

    sociallogin = kwargs.get("sociallogin")
    is_social = bool(sociallogin)
    updated_fields: list[str] = []

    try:
        if hasattr(user, "signup_method"):
            desired_method = "social" if is_social else "manual"
            if getattr(user, "signup_method", None) != desired_method:
                user.signup_method = desired_method
                updated_fields.append("signup_method")

        if hasattr(user, "profile_completed"):
            if getattr(user, "profile_completed", True):
                user.profile_completed = False
                updated_fields.append("profile_completed")

        user_email = str(getattr(user, "email", "") or "").strip().casefold()
        verified_social_email = bool(user_email) and any(
            getattr(address, "verified", False)
            and str(getattr(address, "email", "") or "").strip().casefold()
            == user_email
            for address in getattr(sociallogin, "email_addresses", ())
        )
        if is_social and verified_social_email and hasattr(user, "email_verified_at"):
            if getattr(user, "email_verified_at", None) is None:
                user.email_verified_at = timezone.now()
                updated_fields.append("email_verified_at")

        if updated_fields:
            user.save(update_fields=updated_fields)
            logger.info(
                "User %s flagged for onboarding; updated fields=%s",
                getattr(user, "email", user.pk),
                updated_fields,
            )

    except Exception as exc:
        logger.exception(
            "Error flagging signup completion for user %s: %s",
            getattr(user, "email", user.pk),
            exc,
        )


@receiver(email_confirmed, dispatch_uid="users_handle_email_confirmed_v1")
def handle_email_confirmed(request, email_address, **kwargs):
    """
    Sync allauth EmailAddress confirmations to CustomUser.email_verified_at.
    """
    try:
        if request is not None:
            verification_state = request.session.get("users_email_verification")
            if (
                verification_state
                and verification_state.get("email_address_id") == email_address.pk
            ):
                request.session.pop("users_email_verification", None)

        user = getattr(email_address, "user", None)
        if not user:
            logger.debug("email_confirmed: missing user on email_address")
            return

        updated_fields = []
        if not getattr(user, "email_verified_at", None):
            user.email_verified_at = timezone.now()
            updated_fields.append("email_verified_at")

        if updated_fields:
            user.save(update_fields=updated_fields)
            logger.info(
                "email_confirmed: marked verified for user=%s",
                getattr(user, "email", user.pk),
            )
            # Notify user
            try:
                send_notification(
                    recipient=user,
                    title="Email verified",
                    message="Your email address has been verified successfully.",
                    level="info",
                )
            except Exception:
                logger.debug("email_confirmed notification skipped", exc_info=True)

    except Exception as exc:
        logger.exception("email_confirmed handler failed: %s", exc)


if password_changed:

    @receiver(password_changed, dispatch_uid="users_handle_password_changed_v1")
    def handle_password_changed(request, user, **kwargs):
        """Notify user on password change."""
        try:
            send_notification(
                recipient=user,
                title="Password changed",
                message="Your account password was changed. If this wasn’t you, reset your password immediately.",
                level="warning",
            )
        except Exception:
            logger.debug("password_changed notification skipped", exc_info=True)


if password_set:

    @receiver(password_set, dispatch_uid="users_handle_password_set_v1")
    def handle_password_set(request, user, **kwargs):
        """Notify user when a password is set (e.g., first-time set after invite)."""
        try:
            send_notification(
                recipient=user,
                title="Password set",
                message="A password was set on your account.",
                level="info",
            )
        except Exception:
            logger.debug("password_set notification skipped", exc_info=True)


@receiver(user_logged_in, dispatch_uid="users_handle_logged_in_v1")
def handle_user_logged_in(sender, request, user, **kwargs):
    """
    Create a notification on successful login (includes IP + device id if available).
    """
    try:
        ip = get_client_ip(request) if request else ""
        device = getattr(request, "device", None)
        device_id = getattr(device, "os_fingerprint", None)
        msg_parts = [f"Login from {ip or 'unknown IP'}"]
        if device_id:
            msg_parts.append(f"device {device_id}")
        send_notification(
            user,
            "Login successful",
            "; ".join(msg_parts),
            level="info",
            channel="web",
        )
    except Exception as exc:
        logger.debug(
            "handle_user_logged_in notification skipped: %s", exc, exc_info=True
        )
