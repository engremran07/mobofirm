"""Provider registry: page-type knowledge is contributed, never duplicated.

Each domain app may ship an ``seo_providers`` module that registers one provider per
public view name with :func:`register_provider`. ``apps.seo`` imports those modules
on startup, so an app without SEO installed never imports anything from here, and
``apps.seo`` never imports another app's models at module level.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest

from .contracts import PageSEO
from .models import SEOSettings
from .siteurls import absolute_url


@dataclass
class ProviderContext:
    request: HttpRequest
    context: Mapping[str, Any]
    settings: SEOSettings
    view_name: str
    kwargs: Mapping[str, Any]

    def absolute(self, path: str) -> str:
        return absolute_url(path, self.request)

    def describe(self, explicit: str, derived: str) -> str:
        """Stored description first; derived text only when automation is enabled."""
        if explicit.strip():
            return explicit
        return derived if self.settings.auto_meta_enabled else ""

    def from_context(self, key: str, expected: type) -> Any | None:
        """A view-supplied object, accepted only when it has the expected type."""
        value = self.context.get(key)
        return value if isinstance(value, expected) else None


ProviderFn = Callable[[ProviderContext], PageSEO | None]
_providers: dict[str, ProviderFn] = {}


def register_provider(*view_names: str) -> Callable[[ProviderFn], ProviderFn]:
    def decorator(provider: ProviderFn) -> ProviderFn:
        for name in view_names:
            if name in _providers and _providers[name] is not provider:
                raise ValueError(f"A provider is already registered for {name!r}.")
            _providers[name] = provider
        return provider

    return decorator


def get_provider(view_name: str) -> ProviderFn | None:
    return _providers.get(view_name)


def registered_view_names() -> frozenset[str]:
    return frozenset(_providers)
