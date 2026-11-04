"""
apps.core.middleware.rate_limit_bridge
--------------------------------------
Bridges Django requests to rate limiting functionality.
Prevents brute-force login/signup attempts globally.
"""

import logging
from collections.abc import Callable

from django.http import HttpResponse, JsonResponse

from apps.core.utils.ip import get_client_ip

logger = logging.getLogger(__name__)


def get_rate_limit_service() -> Callable | None:
    """Lazy load rate limit service to avoid circular imports"""
    try:
        # Dynamic import to avoid circular dependency
        from apps.users import services

        if hasattr(services, "rate_limit"):
            return services.rate_limit  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("Failed to load rate limit service: %s", exc)
    return None


class RateLimitBridgeMiddleware:
    """
    Attach global rate limits to auth endpoints and high-risk verbs.
    Keys combine method + IP + device + consent flag to align with device identity/consent.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path.lower()
        method = request.method.lower()
        path_parts = [p for p in path.split("/") if p]
        high_risk_tokens = {"login", "signup", "reset", "token", "password", "mfa"}
        high_risk = any(part in high_risk_tokens for part in path_parts)

        # Rate-limit only unsafe verbs by default; for high-risk auth flows, guard POSTs.
        if method in {"post", "put", "patch", "delete"} or (
            high_risk and method == "post"
        ):
            client_ip = get_client_ip(request) or "unknown"
            device = getattr(request, "device", None)
            device_key = getattr(device, "os_fingerprint", None) if device else None
            consent_state = getattr(request, "consent_categories", None)
            consent_flag = "consented" if consent_state else "unknown"

            key_parts = [
                method,
                client_ip,
                device_key or "nodev",
                consent_flag,
                path[:64],
            ]
            key = "http:" + ":".join(key_parts)

            # Tighter caps for auth-critical endpoints to reduce brute force/enumeration.
            is_auth_path = any(
                part in {"login", "signup", "reset", "password"} for part in path_parts
            )
            max_attempts = 5 if is_auth_path else 10
            window_seconds = 300 if is_auth_path else 300

            allowed = True
            try:
                rate_limit_service = get_rate_limit_service()
                if rate_limit_service:
                    allowed = rate_limit_service.allow_action(
                        key, max_attempts=max_attempts, window_seconds=window_seconds
                    )
            except Exception as exc:
                logger.warning("Rate limiting failed for %s: %s", path, exc)
                # Fail closed only for high-risk paths
                if high_risk:
                    allowed = False
                else:
                    allowed = True

            if not allowed:
                logger.warning("Rate limit exceeded for %s at %s", client_ip, path)
                if request.headers.get("Accept", "").startswith("application/json"):
                    return JsonResponse(
                        {
                            "error": "Too many attempts. Please wait a few minutes before retrying."
                        },
                        status=429,
                    )
                return HttpResponse(status=429)

        return self.get_response(request)
