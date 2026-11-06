"""Typed access to project settings that are injected by ``app.settings``."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def _setting(name: str) -> object:
    return getattr(settings, name)


def base_dir() -> Path:
    return Path(str(_setting("BASE_DIR")))
