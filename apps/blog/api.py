from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import DatabaseError
from django.urls import reverse

from apps.blog import freshness
from apps.blog.models import BlogSettings, Post, PostAccess, PostSource, PostStatus

MAX_CONTRIBUTION_ROWS = 200


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


def stale_post_count() -> int:
    """Live articles not updated within the configured window (editors' signal)."""
    return freshness.stale_post_count()


__all__ = [
    "ContributionSummary",
    "contribution_url",
    "contributions_of",
    "contributions_open_for",
    "get_settings",
    "stale_post_count",
]

# Contribution contract (read by the member "My contributions" hub).


@dataclass(frozen=True, slots=True)
class ContributionSummary:
    """One post the member wrote, with where it stands in review."""

    title: str
    status: str
    status_label: str
    category: str
    submitted_at: datetime
    updated_at: datetime
    review_note: str
    view_url: str
    edit_url: str
    withdraw_url: str
    access: str = "free"
    access_label: str = "Free"
    price_note: str = ""


def _price_note(post: Post) -> str:
    if post.access != PostAccess.PAID:
        return ""
    if post.price is not None:
        return f"Approved price USD {post.price:.2f}"
    if post.proposed_price is not None:
        return f"Price awaiting review (you proposed USD {post.proposed_price:.2f})"
    return "Price awaiting review"


def _status(post: Post) -> tuple[str, str]:
    """A member-facing state: sent-back and withdrawn drafts read differently."""
    if post.status == PostStatus.DRAFT:
        if post.review_note:
            return "returned", "Sent back for changes"
        return "withdrawn", "Draft (withdrawn)"
    if post.status == PostStatus.IN_REVIEW:
        return "in_review", "Awaiting review"
    if post.status == PostStatus.SCHEDULED:
        return "scheduled", "Approved, scheduled"
    if post.status == PostStatus.PUBLISHED:
        return "published", "Published"
    return "archived", "Archived"


def contributions_of(user: Any) -> list[ContributionSummary]:
    """The member's own non-automated posts, newest first (empty when signed out)."""
    if not getattr(user, "is_authenticated", False):
        return []
    posts = (
        Post.objects.exclude(content_source=PostSource.AUTOMATED)
        .filter(author=user)
        .select_related("category")
        .order_by("-created_at", "-pk")[:MAX_CONTRIBUTION_ROWS]
    )
    rows: list[ContributionSummary] = []
    for post in posts:
        code, label = _status(post)
        editable = post.is_community_contribution and post.status in {
            PostStatus.DRAFT,
            PostStatus.IN_REVIEW,
        }
        rows.append(
            ContributionSummary(
                title=post.title,
                status=code,
                status_label=label,
                category=post.category.name if post.category else "",
                submitted_at=post.created_at,
                updated_at=post.updated_at,
                review_note=post.review_note,
                view_url=post.get_absolute_url() if post.is_live else "",
                edit_url=(
                    reverse("blog:contribute_edit", kwargs={"slug": post.slug})
                    if editable
                    else ""
                ),
                withdraw_url=(
                    reverse("blog:contribute_withdraw", kwargs={"slug": post.slug})
                    if editable and post.status == PostStatus.IN_REVIEW
                    else ""
                ),
                access=post.access,
                access_label=str(post.access).capitalize(),
                price_note=_price_note(post),
            )
        )
    return rows


def contribution_url() -> str:
    """The page where a member writes a new post for review."""
    return reverse("blog:contribute")


def contributions_open_for(user: Any) -> bool:
    """Whether the member may submit a blog contribution right now."""
    from apps.blog.services import contributions

    return contributions.is_open_to(user)
