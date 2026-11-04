from __future__ import annotations

"""
apps.core.app_service
---------------------
Simple dynamic app registry + resolver for micro-module APIs.
"""

import importlib  # noqa: E402
import logging  # noqa: E402
from typing import Any  # noqa: E402

from apps.core.models import AppRegistry  # noqa: E402

logger = logging.getLogger(__name__)


class AppService:
    """
    Resolve per-app API modules dynamically, respecting AppRegistry.enable flags.
    """

    _cache: dict[str, Any] = {}

    @classmethod
    def get(cls, app_label: str) -> Any | None:
        key = app_label.strip().lower()
        if not key:
            return None

        # Disabled in registry? return None
        try:
            reg = AppRegistry.get_solo()
            enabled = getattr(reg, f"{key}_enabled", True)
            # For device identity, prefer fail-open so device enforcement/registration still runs.
            if key != "devices" and enabled is False:
                return None
        except Exception:  # noqa: S110
            # Fail-open if registry missing
            pass

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
            except Exception as exc:
                # Log and fail-safe to avoid crashing callers when optional apps misbehave.
                logger.error(
                    "AppService failed",
                    extra={"path": mod_path, "func": "__all__", "error": str(exc)},
                    exc_info=True,
                )
                return None
        return None


__all__ = ["AppService"]
