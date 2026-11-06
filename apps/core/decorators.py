from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse

from apps.core.app_service import app_enabled


def require_app_enabled(app_label: str) -> Callable[[Callable[..., Any]], Any]:
    """Return 404 for a view whose AppRegistry feature flag is switched off."""

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not app_enabled(app_label):
                raise Http404(f"{app_label} is disabled.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
