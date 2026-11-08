"""Scope the entitlement memo to one request."""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponseBase

from . import request_cache


class EntitlementCacheMiddleware:
    """Resolve each user's entitlements at most once per request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        token = request_cache.begin()
        try:
            return self.get_response(request)
        finally:
            request_cache.end(token)
