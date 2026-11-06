"""
apps.users.signals
Centralized user-related signal handlers for the platform.
"""

from __future__ import annotations

import logging

from allauth.account.signals import (
    email_confirmed,
    password_changed,
    password_reset,
    password_set,
    user_signed_up,
)
from django.db import DatabaseError
from django.dispatch import receiver
from django.utils import timezone

from apps.users.api_authentication import revoke_api_tokens
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

    desired_method = "social" if is_social else "manual"
    if user.signup_method != desired_method:
        user.signup_method = desired_method
        updated_fields.append("signup_method")

    if user.profile_completed:
        user.profile_completed = False
        updated_fields.append("profile_completed")

    user_email = str(user.email or "").strip().casefold()
    verified_social_email = bool(user_email) and any(
        getattr(address, "verified", False)
        and str(getattr(address, "email", "") or "").strip().casefold() == user_email
        for address in getattr(sociallogin, "email_addresses", ())
    )
    if is_social and verified_social_email and user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        updated_fields.append("email_verified_at")

    if updated_fields:
        user.save(update_fields=updated_fields)
        logger.info(
            "User %s flagged for onboarding; updated fields=%s",
            user.pk,
            updated_fields,
        )


@receiver(email_confirmed, dispatch_uid="users_handle_email_confirmed_v1")
def handle_email_confirmed(request, email_address, **kwargs):
    """
    Sync allauth EmailAddress confirmations to CustomUser.email_verified_at.
    """
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

    if not user.email_verified_at:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
        logger.info("email_confirmed: marked verified for user=%s", user.pk)
        try:
            send_notification(
                recipient=user,
                title="Email verified",
                message="Your email address has been verified successfully.",
                level="info",
            )
        except DatabaseError:
            logger.warning("email_confirmed notification failed", exc_info=True)


@receiver(password_changed, dispatch_uid="users_handle_password_changed_v1")
@receiver(password_reset, dispatch_uid="users_handle_password_reset_v1")
def handle_password_changed(request, user, **kwargs):
    """Revoke API tokens and notify the user when the password changes."""
    revoke_api_tokens(user)
    try:
        send_notification(
            recipient=user,
            title="Password changed",
            message="Your account password was changed. If this wasn’t you, reset your password immediately.",
            level="warning",
        )
    except DatabaseError:
        logger.warning("password_changed notification failed", exc_info=True)


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
    except DatabaseError:
        logger.warning("password_set notification failed", exc_info=True)
