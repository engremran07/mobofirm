from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)

_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class CorrelationIdMiddleware:
    """
    Adds a per-request correlation ID for traceability across logs.

    An inbound ``X-Request-ID`` is reused only when it is a short, log-safe token;
    anything else is replaced so clients cannot inject arbitrary log content.
    """

    header_name = "X-Request-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        header_key = f"HTTP_{self.header_name.replace('-', '_').upper()}"
        supplied = request.META.get(header_key, "")
        correlation_id = (
            supplied if _VALID_ID.fullmatch(supplied) else str(uuid.uuid4())
        )
        request.correlation_id = correlation_id  # type: ignore[attr-defined]
        response = self.get_response(request)
        response[self.header_name] = correlation_id
        return response
