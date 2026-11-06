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
            "enable_ai_suggestions": False,
            "show_tag_usage": False,
        }
    return {
        "allow_public_suggestions": config.allow_public_suggestions,
        "enable_ai_suggestions": config.enable_ai_suggestions,
        "show_tag_usage": config.show_tag_usage,
    }


__all__ = ["get_settings"]
