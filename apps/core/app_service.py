"""
apps.core.app_service
---------------------
Simple dynamic app registry + resolver for micro-module APIs.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, ClassVar

from apps.core.models import AppRegistry

logger = logging.getLogger(__name__)


class AppService:
    """
    Resolve per-app API modules dynamically, respecting AppRegistry.enable flags.
    """

    _cache: ClassVar[dict[str, Any]] = {}

    @classmethod
    def get(cls, app_label: str) -> Any | None:
        key = app_label.strip().lower()
        if not key:
            return None

        # Disabled in registry? return None. Device identity stays available so
        # registration/enforcement cannot be switched off by the generic flag.
        enabled = getattr(AppRegistry.get_solo(), f"{key}_enabled", True)
        if key != "devices" and enabled is False:
            return None

        if key in cls._cache:
            return cls._cache[key]

        candidates = [
            f"apps.{key}.api",
            f"{key}.api",
        ]
        for mod_path in candidates:
            try:
                module = importlib.import_module(mod_path)
                cls._cache[key] = module
                return module
            except ModuleNotFoundError:
                continue
            except ImportError:
                logger.exception("AppService failed to import %s", mod_path)
                return None
        return None

    @classmethod
    def export(cls, app_label: str, name: str) -> Any | None:
        """A public export of an app's contract, or ``None`` when it is unavailable."""
        return _contract_attribute(cls.get(app_label), name)


def _contract_attribute(contract: Any, name: str) -> Any | None:
    return getattr(contract, name, None)


def app_enabled(app_label: str) -> bool:
    """Return whether the AppRegistry flag for an installed app is switched on."""
    registry = AppRegistry.get_solo()
    return bool(getattr(registry, f"{app_label.strip().lower()}_enabled", True))


__all__ = ["AppService", "app_enabled"]
