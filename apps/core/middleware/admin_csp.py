from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponseBase
from django.urls import Resolver404, resolve
from django.views.decorators.csp import csp_override

# Pages rendered inside the Unfold shell but mounted outside the admin URL prefix:
# whole URL namespaces, and individual views by "namespace:name". They need the admin
# policy because Unfold's Alpine build compiles expressions with ``new Function``.
ADMIN_SHELL_NAMESPACES = frozenset({"admin", "seo"})
ADMIN_SHELL_VIEWS = frozenset(
    {
        "ads:dashboard",
        "tags:manage",
        "tags:duplicates_review",
        "tags:keyword_review",
        "tags:merge",
        "tags:reject_dialog",
        "tags:merge_dialog",
        "comments:moderation_queue",
        "comments:moderation_dialog",
        "storage:status",
    }
)


class AdminContentSecurityPolicyMiddleware:
    """Serve the Unfold shell under ``settings.SECURE_CSP_ADMIN`` and nothing else.

    The public site keeps ``settings.SECURE_CSP``; only responses for the admin and the
    staff pages that extend its shell receive the dedicated policy, built centrally in
    ``app.settings.security``. Django's ``ContentSecurityPolicyMiddleware`` then
    renders it (including the per-request nonce).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if self._uses_admin_shell(request):
            admin_policy = getattr(settings, "SECURE_CSP_ADMIN", settings.SECURE_CSP)
            handler = csp_override(admin_policy)(self.get_response)
            return handler(request)
        return self.get_response(request)

    @staticmethod
    def _uses_admin_shell(request: HttpRequest) -> bool:
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return False
        return bool(
            ADMIN_SHELL_NAMESPACES.intersection(match.namespaces)
            or match.view_name in ADMIN_SHELL_VIEWS
        )
