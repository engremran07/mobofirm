from __future__ import annotations

import logging

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    logger.warning(
        "CSRF validation rejected a request: %s", reason or "no reason given"
    )
    return render(request, "errors/csrf_failure.html", status=403)


def disabled_endpoint(request: HttpRequest) -> HttpResponse:
    """Close a third-party route that must not be reachable."""
    raise Http404("This endpoint is not available.")
