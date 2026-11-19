"""Request middleware: correlation id, request-scoped memo (see the class)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import cast

from django.http import HttpRequest, HttpResponse

from apps.core.utils.logging import (
    bind_correlation_id,
    normalize_correlation_id,
    reset_correlation_id,
)
from apps.core.utils.request_cache import request_scope


class CorrelatedRequest(HttpRequest):
    """An ``HttpRequest`` after :class:`CorrelationIdMiddleware` has run."""

    correlation_id: str


class CorrelationIdMiddleware:
    """
    Opens the per-request context: correlation id and the request-scoped memo.

    An inbound ``X-Request-ID`` is reused only when it is a short, log-safe token;
    anything else is replaced so clients cannot inject arbitrary log content. The id
    is exposed as ``request.correlation_id``, bound for the whole request so every
    log record carries it (``apps.core.utils.logging``), echoed on the response and
    forwarded to Celery tasks (``apps.core.celery_context``). Only the opaque id is
    logged: never a user id, address or other personal data.

    The request scope (``apps.core.utils.request_cache``) lets hot lookups such as
    the application registry run once per request instead of once per caller; it is
    always closed in the ``finally`` block, even when the view raises.
    """

    header_name = "X-Request-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        header_key = f"HTTP_{self.header_name.replace('-', '_').upper()}"
        correlation_id = normalize_correlation_id(
            request.META.get(header_key, "")
        ) or str(uuid.uuid4())
        cast("CorrelatedRequest", request).correlation_id = correlation_id
        token = bind_correlation_id(correlation_id)
        try:
            with request_scope():
                response = self.get_response(request)
        finally:
            reset_correlation_id(token)
        response[self.header_name] = correlation_id
        return response
