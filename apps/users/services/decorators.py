from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any, TypeVar

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from .consent import check as consent_check

View = TypeVar("View", bound=Callable[..., HttpResponse])


def consent_required(categories: str | Iterable[str]) -> Callable[[View], View]:
    required = (categories,) if isinstance(categories, str) else tuple(categories)

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not all(consent_check(category, request) for category in required):
                return HttpResponseForbidden("Consent required.")
            return view(request, *args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorate
