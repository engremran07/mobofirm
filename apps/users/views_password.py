"""Live password feedback endpoint (sign-up, reset, change-password, profile).

A debounced, CSRF-protected POST: the page sends what the user has typed and gets
back one pass/fail per configured password validator, computed by
``django.contrib.auth.password_validation`` through ``services.password_rules``.
The password is never logged, stored or echoed; the response is not cacheable.
"""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from apps.core.utils.ip import get_client_ip
from apps.users.services.password_rules import (
    MAX_PASSWORD_LENGTH,
    SIMILARITY_FIELDS,
    evaluate_password,
)
from apps.users.services.rate_limit import allow_action

# Typing is debounced client side; this only stops scripted abuse of the oracle.
RULE_CHECKS_PER_MINUTE = 120
FIELD_VALUE_LIMIT = 254


@require_POST
@sensitive_post_parameters("password")
@never_cache
def password_rules_check(request: HttpRequest) -> JsonResponse:
    """Per-rule pass/fail for ``password``, using the live form values for similarity."""
    if not allow_action(
        f"password-rules:{get_client_ip(request) or 'unknown'}",
        max_attempts=RULE_CHECKS_PER_MINUTE,
        window_seconds=60,
    ):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    password = request.POST.get("password", "")
    if len(password) > MAX_PASSWORD_LENGTH:
        return JsonResponse({"ok": False, "error": "too_long"}, status=400)
    values = {
        name: request.POST.get(name, "")[:FIELD_VALUE_LIMIT]
        for name in SIMILARITY_FIELDS
    }
    account = request.user if request.user.is_authenticated else None
    results = evaluate_password(password, values, account)
    return JsonResponse(
        {
            "ok": True,
            "valid": all(result.passed for result in results),
            "rules": [
                {
                    "id": result.id,
                    "passed": result.passed,
                    "checked": result.checked,
                    "message": result.message,
                }
                for result in results
            ],
        }
    )
