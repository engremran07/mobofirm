from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from apps.core.utils.ip import get_client_ip
from apps.users.services.rate_limit import allow_action

logger = logging.getLogger(__name__)


class PasswordResetThrottleMiddleware(MiddlewareMixin):
    """
    Tight throttle for password reset initiation to prevent email spam and abuse.
    Applies to POST /accounts/password/reset/.
    """

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        if (request.method or "").lower() != "post":
            return None

        path = request.path.lower().rstrip("/")
        if not path.endswith("/accounts/password/reset"):
            return None

        email = (request.POST.get("email") or "").strip().lower()
        client_ip = get_client_ip(request) or "unknown"
        per_combo_key = f"pwreset_init:{client_ip}:{email or 'noemail'}"
        per_email_key = f"pwreset_email:{email or 'noemail'}"
        per_ip_key = f"pwreset_ip:{client_ip}"

        allowed = True
        try:
            # FINDING-0016: fail_open=False — auth paths must block on Redis failure.
            # Tight IP+email throttle
            if not allow_action(
                per_combo_key, max_attempts=3, window_seconds=300, fail_open=False
            ):
                allowed = False
            # Per-email longer window to prevent cross-IP spamming
            if allowed and not allow_action(
                per_email_key, max_attempts=10, window_seconds=3600, fail_open=False
            ):
                allowed = False
            # Per-IP global guard
            if allowed and not allow_action(
                per_ip_key, max_attempts=10, window_seconds=900, fail_open=False
            ):
                allowed = False
        except Exception as exc:
            # FINDING-0016: exception here means allow_action itself threw before returning;
            # treat this the same as fail_open=False for safety.
            logger.warning("Reset throttle backend failure: %s", exc)
            allowed = False

        if not allowed:
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

        return None
