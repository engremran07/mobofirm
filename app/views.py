from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

logger = logging.getLogger(__name__)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    logger.warning("CSRF validation rejected a request")
    return render(request, "errors/csrf_failure.html", status=403)
