from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from app.version import app_version


class AppVersionHeaderMiddleware:
    """Stamp every response with ``X-App-Version`` (the release version only).

    The value comes from ``app.version``, the same source as the footer, the admin
    and ``/health/``; build metadata (git SHA) is deliberately not sent on every
    response.
    """

    header_name = "X-App-Version"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response[self.header_name] = app_version()
        return response
