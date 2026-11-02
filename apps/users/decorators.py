"""
apps/users/decorators.py

Custom decorators for user-related functionality including:
- Email verification requirement for sensitive actions
- Profile completion checks
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


def email_verification_required(
    view_func: Callable | None = None,
    redirect_url: str = "users:verify_email_required",
    message: str | None = None,
):
    """
    Decorator that requires email verification for sensitive actions.

    Social login users are automatically verified (by OAuth provider).
    Manual signup users need explicit email verification.

    Usage:
        @email_verification_required
        def sensitive_view(request):
            ...

        @email_verification_required(message="Please verify your email first")
        def another_view(request):
            ...

    Args:
        view_func: The view function to wrap
        redirect_url: URL name to redirect unverified users to
        message: Custom message to show to unverified users
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user

            # Must be authenticated
            if not user.is_authenticated:
                return redirect("account_login")

            # Social login users are already verified by OAuth provider
            signup_method = getattr(user, "signup_method", None)
            if signup_method == "social":
                return func(request, *args, **kwargs)

            # Check if email is verified
            email_verified_at = getattr(user, "email_verified_at", None)
            if email_verified_at is not None:
                return func(request, *args, **kwargs)

            # Also check allauth EmailAddress model
            try:
                from allauth.account.models import EmailAddress

                email_obj = EmailAddress.objects.filter(
                    user=user, email=getattr(user, "email", ""), verified=True
                ).first()
                if email_obj:
                    # Update user's email_verified_at if not set
                    if (
                        hasattr(user, "email_verified_at")
                        and getattr(user, "email_verified_at", None) is None
                    ):
                        from django.utils import timezone

                        user.email_verified_at = timezone.now()  # type: ignore[attr-defined]
                        user.save(update_fields=["email_verified_at"])  # type: ignore[call-arg]
                    return func(request, *args, **kwargs)
            except Exception as e:
                logger.warning(f"Failed to check EmailAddress: {e}")

            # User needs to verify email
            msg = message or _("Please verify your email address before continuing.")
            messages.warning(request, msg)

            # Store the intended URL for post-verification redirect
            request.session["next_after_verify"] = request.get_full_path()

            return redirect(redirect_url)

        return wrapper

    # Allow using @email_verification_required without parentheses
    if view_func is not None:
        return decorator(view_func)
    return decorator


def profile_complete_required(
    view_func: Callable | None = None,
    redirect_url: str = "users:tell_us_about_you",
    message: str | None = None,
):
    """
    Decorator that requires a completed profile before accessing the view.

    Usage:
        @profile_complete_required
        def dashboard(request):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user

            # Must be authenticated
            if not user.is_authenticated:
                return redirect("account_login")

            # Check if profile is completed
            profile_completed = getattr(user, "profile_completed", True)
            if profile_completed:
                return func(request, *args, **kwargs)

            # Profile not complete
            msg = message or _("Please complete your profile to continue.")
            messages.info(request, msg)

            return redirect(redirect_url)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


def social_user_profile_required(
    view_func: Callable | None = None,
    redirect_url: str = "users:tell_us_about_you",
):
    """
    Decorator specifically for social login users who haven't completed profile.

    Social users get redirected to profile completion on first login.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user

            if not user.is_authenticated:
                return redirect("account_login")

            # Only check social login users
            signup_method = getattr(user, "signup_method", None)
            if signup_method != "social":
                return func(request, *args, **kwargs)

            # Check if profile completed
            profile_completed = getattr(user, "profile_completed", True)
            if profile_completed:
                return func(request, *args, **kwargs)

            return redirect(redirect_url)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator
