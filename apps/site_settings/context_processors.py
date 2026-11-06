from __future__ import annotations

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from .services import get_site_settings


def site_settings(request: HttpRequest) -> dict[str, object]:
    """Expose the typed settings snapshot to templates as ``site_settings``.

    Lazy, so error pages and partials that never read it do not touch the cache.
    """
    return {"site_settings": SimpleLazyObject(get_site_settings)}
