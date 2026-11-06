from __future__ import annotations

from typing import Any

from django.db import DatabaseError

from apps.blog.models import BlogSettings


def get_settings() -> dict[str, Any]:
    try:
        config = BlogSettings.get_solo()
    except DatabaseError:
        return {
            "enable_blog": False,
            "enable_blog_comments": False,
            "allow_user_blog_posts": False,
        }
    return {
        "enable_blog": config.enable_blog,
        "enable_blog_comments": config.enable_blog_comments,
        "allow_user_blog_posts": config.allow_user_blog_posts,
    }


__all__ = ["get_settings"]
