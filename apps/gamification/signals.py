from __future__ import annotations

from typing import Any

from apps.gamification.services import sync_defaults


def sync_defaults_after_migrate(**kwargs: Any) -> None:
    sync_defaults()
