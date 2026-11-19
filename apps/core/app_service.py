"""
apps.core.app_service
---------------------
Simple dynamic app registry + resolver for micro-module APIs.

Runtime configuration authority
-------------------------------
"Is app X on?" has one answer, in this order (first match wins):

1. ``AppRegistry.<app>_enabled`` (staff-editable singleton) is the only *switch* for
   an app's routes, API and cross-app contract: ``app_enabled`` / ``AppService`` /
   ``require_app_enabled`` all read it, once per request.
2. The app's own settings row (``BlogSettings``, ``SEOSettings`` ...) tunes behaviour
   *inside* an app that is on; it cannot switch the app back on.
3. Environment flags (``MAINTENANCE_MODE``, ``REQUIRE_MFA`` ...) are deployment-level
   forces read through the typed accessors in ``apps.core.runtime_config``; they are
   never a second answer to question 1.

A label without a registry flag is a programming error: it is logged at error level
and treated as OFF (the old behaviour silently treated a typo as ON, so a disable
switch could stop working unnoticed). ``tests/backend/integrations`` asserts that
every literal label used across the apps has a flag.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, ClassVar

from apps.core.models import AppRegistry

logger = logging.getLogger(__name__)

# Device registration/enforcement cannot be switched off by the generic flag; the
# dedicated ``AppRegistry.device_identity_enabled`` pauses automatic registration.
ALWAYS_ENABLED = frozenset({"devices"})


def current_registry() -> AppRegistry:
    """The application registry row (one query per request, see ``AppRegistry``)."""
    return AppRegistry.get_solo()


def registry_flag_names() -> frozenset[str]:
    """Every app label that has an ``AppRegistry`` flag."""
    suffix = "_enabled"
    return frozenset(
        field.name.removesuffix(suffix)
        for field in AppRegistry._meta.get_fields()
        if field.name.endswith(suffix)
    )


def app_enabled(app_label: str) -> bool:
    """Whether the ``AppRegistry`` flag of an installed app is switched on."""
    key = app_label.strip().lower()
    if key in ALWAYS_ENABLED:
        return True
    flag = f"{key}_enabled"
    registry = current_registry()
    if not hasattr(registry, flag):
        logger.error(
            "AppRegistry has no %r flag: %r is not a registered app label, "
            "treating it as disabled.",
            flag,
            app_label,
        )
        return False
    return bool(getattr(registry, flag))


class AppService:
    """
    Resolve per-app API modules dynamically, respecting AppRegistry.enable flags.
    """

    _cache: ClassVar[dict[str, Any]] = {}

    @classmethod
    def get(cls, app_label: str) -> Any | None:
        """The app's ``api`` module, or ``None`` when it is off, unknown or missing."""
        key = app_label.strip().lower()
        if not key:
            return None

        # Disabled in registry (or unknown label)? return None.
        if not app_enabled(key):
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


__all__ = [
    "ALWAYS_ENABLED",
    "AppService",
    "app_enabled",
    "current_registry",
    "registry_flag_names",
]
