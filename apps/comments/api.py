from __future__ import annotations

from typing import Any

from apps.comments.models import CommentSettings


def get_settings() -> dict[str, Any]:
    try:
        s = CommentSettings.get_solo()
        return {
            "enable_comments": bool(getattr(s, "enable_comments", True)),
            "allow_anonymous": bool(getattr(s, "allow_anonymous", False)),
            "enable_ai_moderation": bool(getattr(s, "enable_ai_moderation", True)),
        }
    except Exception:
        return {
            "enable_comments": True,
            "allow_anonymous": False,
            "enable_ai_moderation": True,
        }


__all__ = ["get_settings"]
