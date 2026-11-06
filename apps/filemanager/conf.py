"""Typed access to the file manager settings defined in ``app.settings``."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings


def _setting(name: str) -> object:
    return getattr(settings, name)


def root() -> Path:
    return Path(str(_setting("FILEMANAGER_ROOT")))


def max_upload_bytes() -> int:
    return int(str(_setting("FILEMANAGER_MAX_UPLOAD_BYTES")))


def user_quota_bytes() -> int:
    return int(str(_setting("FILEMANAGER_USER_QUOTA_BYTES")))
