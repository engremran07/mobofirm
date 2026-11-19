from __future__ import annotations

from collections.abc import Iterable

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from apps.site_settings.services import get_site_settings
from apps.users.services.profile_completion import (
    profile_completion_url,
    safe_next_url,
)


class EnforceProfileCompletionMiddleware:
    """
    Sends social-signup users to onboarding until their profile is complete.

    Active only when the site setting ``require_profile_completion`` (default:
    ``REQUIRE_PROFILE_COMPLETION``) is true. Auth, admin, static, health and the
    onboarding page itself are never intercepted, so there is no redirect loop.

    The gate cannot be skipped by a header. Browser pages are redirected; an HTMX
    request gets an ``HX-Redirect`` to onboarding; an AJAX or JSON client gets a
    ``403`` JSON answer naming the onboarding page. Token-authenticated API calls
    never reach this check (they carry no session user) and are refused at token
    issuance instead (``ApiLoginView``).
    """

    PROFILE_URL_NAME = "users:tell_us_about_you"

    SAFE_URL_NAMES: Iterable[str] = {
        PROFILE_URL_NAME,
        "users:locale_hint",
        "users:password_rules",
        "users:account_login",
        "users:account_logout",
        "users:account_signup",
        "users:api_current_user",
        "users:api_logout",
        "users:mfa_setup",
        "users:mfa_verify",
        "users:mfa_backup_codes",
        "account_login",
        "account_logout",
        "account_signup",
        "account_reset_password",
        "account_reset_password_done",
        "account_reset_password_from_key",
        "account_reset_password_from_key_done",
    }

    SAFE_PATH_PREFIXES: Iterable[str] = (
        "/admin",
        "/static",
        "/media",
        "/health",
        "/site",
        "/consent",
        "/.well-known",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not get_site_settings().require_profile_completion:
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not (user and user.is_authenticated) or user.profile_completed:
            return self.get_response(request)
        if user.is_staff and request.path.startswith("/admin"):
            return self.get_response(request)
        # Password sign-ups complete their profile during registration.
        if not user.socialaccount_set.exists():
            return self.get_response(request)

        path = request.path
        if path.startswith(tuple(self.SAFE_PATH_PREFIXES)):
            return self.get_response(request)
        if path == reverse(self.PROFILE_URL_NAME):
            return self.get_response(request)

        try:
            match = request.resolver_match or resolve(path)
        except Resolver404:
            return self.get_response(request)
        if match.view_name in self.SAFE_URL_NAMES:
            return self.get_response(request)

        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = profile_completion_url("")
            return response
        if (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.content_type == "application/json"
        ):
            return JsonResponse(
                {
                    "detail": "Complete your profile before continuing.",
                    "profile_completion_required": True,
                    "onboarding_url": profile_completion_url(""),
                },
                status=403,
            )

        # Return the user to the page they asked for once onboarding is done.
        next_url = (
            safe_next_url(request, request.get_full_path())
            if request.method == "GET"
            else ""
        )
        return redirect(profile_completion_url(next_url))
