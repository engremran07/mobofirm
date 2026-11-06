from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from apps.core.utils.ip import get_client_ip
from apps.users.services.rate_limit import CACHE_ERRORS, allow_action


class PasswordResetThrottleMiddleware(MiddlewareMixin):
    """
    Tight throttle for password reset initiation to prevent email spam and abuse.
    Applies to POST /accounts/password/reset/ and fails closed if the cache is down.
    """

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        if (request.method or "").lower() != "post":
            return None

        path = request.path.lower().rstrip("/")
        if not path.endswith("/accounts/password/reset"):
            return None

        email = (request.POST.get("email") or "").strip().lower()
        client_ip = get_client_ip(request) or "unknown"
        limits = (
            (f"pwreset_init:{client_ip}:{email or 'noemail'}", 3, 300),
            (f"pwreset_email:{email or 'noemail'}", 10, 3600),
            (f"pwreset_ip:{client_ip}", 10, 900),
        )
        try:
            allowed = all(
                allow_action(
                    key, max_attempts=attempts, window_seconds=window, fail_open=False
                )
                for key, attempts, window in limits
            )
        except CACHE_ERRORS:
            allowed = False

        if allowed:
            return None
        if request.headers.get("Accept", "").startswith("application/json"):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Too many reset attempts. Try again in a few minutes.",
                },
                status=429,
            )
        return HttpResponse(
            "Too many reset attempts. Try again in a few minutes.", status=429
        )
