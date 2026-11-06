"""Settings service implementation.

Core reads site settings through ``apps.site_settings`` (the single runtime
authority) and never imports its models.
"""

from __future__ import annotations

from typing import Any

from apps.core.interfaces import SettingsProvider
from apps.site_settings.services import public_settings


class SiteSettingsProvider(SettingsProvider):
    """Serves the non-sensitive, public site settings by lower-case key."""

    def get(self, key: str, default: Any = None) -> Any:
        return public_settings().get(key.lower(), default)

    def get_all(self) -> dict[str, Any]:
        return dict(public_settings())


# Global instance
settings_provider = SiteSettingsProvider()
