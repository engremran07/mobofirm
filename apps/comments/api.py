from __future__ import annotations

from typing import Any

from django.db import DatabaseError

from apps.comments.models import CommentSettings


def get_settings() -> dict[str, Any]:
    try:
        config = CommentSettings.get_solo()
    except DatabaseError:
        return {
            "enable_comments": False,
            "auto_approve_clean": False,
            "max_links": 0,
            "report_threshold": 1,
        }
    return {
        "enable_comments": config.enable_comments,
        "auto_approve_clean": config.auto_approve_clean,
        "max_links": config.max_links,
        "report_threshold": config.report_threshold,
    }


__all__ = ["get_settings"]
