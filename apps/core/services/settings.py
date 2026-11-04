"""
Settings Service Implementation
Provides access to site settings without direct model imports in core.
"""

from typing import Any

from django.conf import settings

from apps.core.interfaces import SettingsProvider


class DjangoSettingsProvider(SettingsProvider):
    """Implementation that uses Django's settings module"""

    def get(self, key: str, default: Any = None) -> Any:
        """Return an explicitly configured Django setting or the caller default."""
        return getattr(settings, key, default)

    def get_all(self) -> dict[str, Any]:
        """Get all settings as dictionary"""
        public_settings = (
            "SITE_NAME",
            "SITE_DESCRIPTION",
            "LANGUAGE_CODE",
            "TIME_ZONE",
            "RECAPTCHA_ENABLED",
        )
        return {
            key.lower(): getattr(settings, key)
            for key in public_settings
            if hasattr(settings, key)
        }


# Global instance
settings_provider = DjangoSettingsProvider()
