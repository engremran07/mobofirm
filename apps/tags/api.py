from __future__ import annotations

from typing import Any

from django.db import DatabaseError

from apps.tags.models import TagsSettings


def get_settings() -> dict[str, Any]:
    try:
        config = TagsSettings.get_solo()
    except DatabaseError:
        return {
            "allow_public_suggestions": False,
            "automatic_suggestions_enabled": False,
            "show_tag_usage": False,
        }
    return {
        "allow_public_suggestions": config.allow_public_suggestions,
        "automatic_suggestions_enabled": config.automatic_suggestions_enabled,
        "show_tag_usage": config.show_tag_usage,
    }


__all__ = ["get_settings"]
