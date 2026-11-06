from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.site_settings.services import get_site_settings


class MaintenanceModeMiddleware:
    """
    Serve a 503 page to everyone except staff while maintenance mode is on.

    Maintenance mode comes from the runtime site settings (the ``MAINTENANCE_MODE``
    environment value forces it on). Health probes, static assets, the admin, the
    sign-in pages and the public ``/site/`` assets (theme, verification files)
    stay reachable so operators can verify the instance and finish the maintenance.
    """

    EXEMPT_PREFIXES = (
        "/health/",
        "/static/",
        "/media/",
        "/site/",
        "/admin/",
        "/users/login/",
        "/accounts/",
    )

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        snapshot = get_site_settings()
        if not snapshot.maintenance_mode:
            return self.get_response(request)
        if request.path.startswith(self.EXEMPT_PREFIXES):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return self.get_response(request)
        response = render(
            request,
            "errors/503.html",
            {"message": snapshot.maintenance_message},
            status=503,
        )
        response["Retry-After"] = "300"
        return response
