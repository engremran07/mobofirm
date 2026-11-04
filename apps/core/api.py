from __future__ import annotations

import logging
from typing import Any

from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class BaseAPIView(APIView):
    """
    Base API view with structured logging helpers and standard pagination.
    """

    pagination_class = StandardResultsSetPagination

    def log_event(self, level: str, message: str, **extra: Any) -> None:
        try:
            cid = getattr(getattr(self, "request", None), "correlation_id", None)
            logger.log(
                getattr(logging, level.upper(), logging.INFO),
                message,
                extra={"event": extra, "correlation_id": cid},
            )
        except Exception:
            return

    def paginate(self, queryset):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        return page, paginator

    def ok(
        self, data: dict[str, Any] | None = None, status_code: int = status.HTTP_200_OK
    ) -> Response:
        return Response({"ok": True, **(data or {})}, status=status_code)

    def error(
        self, error: str, status_code: int = status.HTTP_400_BAD_REQUEST
    ) -> Response:
        return Response({"ok": False, "error": error}, status=status_code)
