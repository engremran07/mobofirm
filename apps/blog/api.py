from __future__ import annotations

from typing import Any

from apps.blog.models import BlogSettings


def get_settings() -> dict[str, Any]:
    try:
        s = BlogSettings.get_solo()
        return {
            "enable_blog": bool(getattr(s, "enable_blog", True)),
            "enable_blog_comments": bool(getattr(s, "enable_blog_comments", True)),
            "allow_user_blog_posts": bool(getattr(s, "allow_user_blog_posts", False)),
        }
    except Exception:
        return {
            "enable_blog": True,
            "enable_blog_comments": True,
            "allow_user_blog_posts": False,
        }


__all__ = ["get_settings"]
