"""Signal receivers owned by the wallet."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig


def sync_economy_rows(sender: AppConfig, **kwargs: Any) -> None:
    """Create rule and price rows for registered events once the wallet is migrated."""
    if sender.label != "wallet":
        return
    from .services import sync_registry

    sync_registry()
