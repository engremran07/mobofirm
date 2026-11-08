"""Capture ``?ref=CODE`` on any page into the session for the next sign-up."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .services import QUERY_PARAM, capture_code


class ReferralCaptureMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        value = request.GET.get(QUERY_PARAM) if request.method == "GET" else None
        if value:
            capture_code(request, value)
        return self.get_response(request)
