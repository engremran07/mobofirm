from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import Any, cast

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

from apps.consent.services import has_consent


def consent_required[V: Callable[..., HttpResponse]](
    categories: str | Iterable[str],
) -> Callable[[V], V]:
    """Answer 403 unless the visitor allows every listed category."""
    required = (categories,) if isinstance(categories, str) else tuple(categories)

    def decorate(view: V) -> V:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not all(has_consent(request, category) for category in required):
                return HttpResponseForbidden("Consent required.")
            return view(request, *args, **kwargs)

        return cast(V, wrapped)

    return decorate
