from __future__ import annotations

from collections.abc import Callable

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)

from .models import Redirect
from .policy import is_noindex_path
from .redirects import is_safe_target


class RedirectFallbackMiddleware:
    """Serve active ``Redirect`` rows when a GET/HEAD request would otherwise 404."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in {"GET", "HEAD"}:
            return response
        redirect = Redirect.objects.filter(source=request.path, is_active=True).first()
        if (
            redirect is None
            or redirect.target == request.path
            or not is_safe_target(redirect.target)
        ):
            return response
        redirect_class = (
            HttpResponsePermanentRedirect
            if redirect.is_permanent
            else HttpResponseRedirect
        )
        return redirect_class(redirect.target)


class RobotsHeaderMiddleware:
    """Send ``X-Robots-Tag: noindex, nofollow`` for private and download responses.

    The header covers non-HTML bodies (JSON, files) where no ``<meta>`` tag exists.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if "X-Robots-Tag" in response:
            return response
        is_json = response.get("Content-Type", "").startswith("application/json")
        if is_noindex_path(request.path) or is_json:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
