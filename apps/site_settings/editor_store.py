"""Database-backed editor profile overrides, cached and invalidated on change."""

from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save

from apps.core.editor.spec import ProfileSpec, register_profile_source

from .editor_models import EditorProfile

logger = logging.getLogger(__name__)

CACHE_KEY = "site_settings:editor_profiles:v1"
CACHE_TTL_SECONDS = 300


def load_profiles() -> dict[str, ProfileSpec]:
    """Every stored profile keyed by context; empty when the table is unavailable."""
    cached: dict[str, ProfileSpec] | None = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        profiles = {row.context: row.to_spec() for row in EditorProfile.objects.all()}
    except DatabaseError:
        logger.warning("Editor profiles are unavailable; using code defaults")
        return {}
    cache.set(CACHE_KEY, profiles, CACHE_TTL_SECONDS)
    return profiles


def profile_from_database(context: str) -> ProfileSpec | None:
    """The administrator's override for ``context`` or ``None``."""
    return load_profiles().get(context)


def clear_profile_cache(**_kwargs: Any) -> None:
    """Drop the cached profiles so edits apply immediately."""
    cache.delete(CACHE_KEY)


def register() -> None:
    """Plug the database source into the core editor and watch for edits."""
    register_profile_source(profile_from_database)
    post_save.connect(
        clear_profile_cache,
        sender=EditorProfile,
        dispatch_uid="editor_profile_saved",
    )
    post_delete.connect(
        clear_profile_cache,
        sender=EditorProfile,
        dispatch_uid="editor_profile_deleted",
    )
