import logging
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class CorrelationIdMiddleware:
    """
    Adds a per-request correlation ID for traceability across logs.
    """

    header_name = "X-Request-ID"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Django stores headers as HTTP_<header> with underscores
        header_key = f"HTTP_{self.header_name.replace('-', '_').upper()}"
        correlation_id = request.META.get(header_key) or str(uuid.uuid4())
        request.correlation_id = correlation_id  # type: ignore[attr-defined]
        response = self.get_response(request)
        response[self.header_name] = correlation_id
        return response
