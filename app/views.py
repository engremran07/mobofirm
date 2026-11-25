"""Project-level responses for rejected, disabled, and legacy routes."""

from __future__ import annotations

import logging
from urllib.parse import quote

from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
)
from django.shortcuts import render

from apps.core.views import staff_console_template

logger = logging.getLogger(__name__)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """Render the app-owned response for a request rejected by CSRF validation."""
    logger.warning(
        "CSRF validation rejected a request: %s", reason or "no reason given"
    )
    template = staff_console_template(
        request, "errors/csrf_failure.html", "admin/csrf_failure.html"
    )
    return render(request, template, status=403)


def disabled_endpoint(request: HttpRequest) -> HttpResponse:
    """Close a third-party route that must not be reachable."""
    raise Http404("This endpoint is not available.")


class HttpResponsePermanentRedirectKeepMethod(HttpResponsePermanentRedirect):
    """308: a permanent redirect that makes clients repeat the same method and body."""

    status_code = 308


def legacy_shop_redirect(request: HttpRequest) -> HttpResponse:
    """Every ``/shop/...`` address moved to ``/pricing/...`` (same path, same query).

    ``/shop/`` and ``/shop/pricing/`` land on the plans page itself. Reads get a 301;
    other methods get a 308 so an old form or webhook target is never turned into a GET.
    """
    rest = request.path_info.removeprefix("/shop/")
    if rest in {"", "pricing/"}:
        rest = ""
    target = f"/pricing/{quote(rest)}"
    query = request.META.get("QUERY_STRING", "")
    if query:
        target = f"{target}?{query}"
    if request.method in {"GET", "HEAD"}:
        return HttpResponsePermanentRedirect(target)
    return HttpResponsePermanentRedirectKeepMethod(target)
