"""Authorization and feature-flag helpers for the blog (single source of truth)."""

from __future__ import annotations

from django.db import DatabaseError

from apps.blog.models import BlogSettings, Post, PostStatus
from apps.core.app_service import AppService, app_enabled
from apps.users.models import CustomUser


def blog_settings() -> BlogSettings:
    return BlogSettings.get_solo()


def blog_enabled_for(user) -> bool:
    """The blog is public when enabled; staff may always reach it."""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    try:
        return app_enabled("blog") and blog_settings().enable_blog
    except DatabaseError:
        return False


def comments_enabled() -> bool:
    """Comments need the app flag, the comments switch and the blog comments switch."""
    from apps.comments.models import CommentSettings

    try:
        return (
            app_enabled("comments")
            and CommentSettings.get_solo().enable_comments
            and blog_settings().enable_blog_comments
        )
    except DatabaseError:
        return False


def is_staff_user(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_staff or user.is_superuser)
    )


def has_editor_role(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and user.has_role(CustomUser.Roles.EDITOR)
    )


def can_manage_all_posts(user) -> bool:
    return is_staff_user(user) or has_editor_role(user)


def can_author_posts(user) -> bool:
    """Staff, editors, authors, and any member when user posting is switched on."""
    if not getattr(user, "is_authenticated", False):
        return False
    if can_manage_all_posts(user) or user.has_role(CustomUser.Roles.AUTHOR):
        return True
    return blog_settings().allow_user_blog_posts


def can_manage_post(user, post: Post) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return can_manage_all_posts(user) or post.author_id == user.pk


def can_view_post(user, post: Post) -> bool:
    """Live posts are public; others are visible to staff and their author."""
    return post.is_live or can_manage_post(user, post)


def can_edit_post(user, post: Post) -> bool:
    """Staff and editors may edit any post; authors only while it is a draft."""
    if can_manage_all_posts(user):
        return True
    return can_manage_post(user, post) and post.status == PostStatus.DRAFT


def is_approved_seller(user) -> bool:
    """Marketplace seller approval, reached only through the marketplace contract."""
    check = AppService.export("marketplace", "is_active_seller")
    return bool(callable(check) and check(user))


def can_set_premium_access(user) -> bool:
    """Who may mark a post featured or paid.

    Same trust rule as marketplace sellers: staff, editors, authors with the author
    role and approved sellers. Everyone else submits free posts.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        can_manage_all_posts(user)
        or user.has_role(CustomUser.Roles.AUTHOR)
        or is_approved_seller(user)
    )
