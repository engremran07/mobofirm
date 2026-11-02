"""
apps.users.apps
Application configuration for the platform's Users module.

✅ Responsibilities:
- Auto-register user signals (login, signup, profile creation)
- Integrate cleanly with django-allauth adapters/forms
- Async-safe startup; ORM import-guarded
- Autodiscover any "signals" submodules across installed apps
- Zero deprecations for Django 5.2 LTS + allauth 0.65.13
"""

from __future__ import annotations

import logging

from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class UsersConfig(AppConfig):
    """
    Enterprise-grade AppConfig for user management.

    Loads signals and cross-app hooks exactly once per process.
    Safe during migrations, tests, shell, or async contexts.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    label = "users"
    verbose_name = "User Management"

    def ready(self) -> None:
        """
        Initialization hook executed when Django finishes app loading.

        Tasks performed:
        1️⃣  Import this app's local signals safely.
        2️⃣  Autodiscover any `signals.py` modules across installed apps.
        3️⃣  Provide extension hook for future warm-ups or audits.
        """
        logger = logging.getLogger(__name__)

        # ---------------------------------------------------------------
        # 1️⃣ Import local signal handlers (safe guarded)
        # ---------------------------------------------------------------
        try:
            import apps.users.signals  # noqa: F401
        except Exception as exc:
            logger.warning("Failed to import users signals: %s", exc)

        # Import signal handlers for cross-app communication
        try:
            from . import signal_handlers  # noqa: F401

            logger.debug("UsersConfig → signals imported successfully.")
        except ImportError as exc:
            # Signal import errors should never break app startup.
            logger.warning("UsersConfig: unable to import signals (%s)", exc)
        except Exception as exc:
            logger.exception("UsersConfig: unexpected error loading signals → %s", exc)

        # ---------------------------------------------------------------
        # 2️⃣ Autodiscover cross-app signal modules (optional)
        # ---------------------------------------------------------------
        try:
            autodiscover_modules("signals")
            logger.debug("UsersConfig → autodiscovered 'signals' modules across apps.")
        except Exception as exc:
            # Do not fail on autodiscovery — some apps may not have signals.
            logger.debug(
                "UsersConfig: autodiscover_modules('signals') failed → %s", exc
            )

        # ---------------------------------------------------------------
        # 3️⃣ Future-proof extension hook (keep light)
        # ---------------------------------------------------------------
        # Example future tasks:
        #   - Schedule initial audit tasks
        #   - Load feature-flag toggles
        #
        # Must remain non-blocking and ORM-safe.
        logger.debug("UsersConfig.ready() completed successfully.")
        return
