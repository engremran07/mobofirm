from __future__ import annotations

from typing import Any

from apps.tags.models import TagsSettings


def get_settings() -> dict[str, Any]:
    try:
        s = TagsSettings.get_solo()
        return {
            "allow_public_suggestions": bool(
                getattr(s, "allow_public_suggestions", True)
            ),
            "enable_ai_suggestions": bool(getattr(s, "enable_ai_suggestions", True)),
            "show_tag_usage": bool(getattr(s, "show_tag_usage", True)),
        }
    except Exception:
        return {
            "allow_public_suggestions": True,
            "enable_ai_suggestions": True,
            "show_tag_usage": True,
        }


__all__ = ["get_settings"]
