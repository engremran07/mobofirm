"""Read-side comment queries: the threaded tree shown under an article."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q

from apps.blog.models import Post
from apps.comments.models import Comment, CommentVote
from apps.comments.services.comment_service import EDIT_WINDOW, MAX_THREAD_DEPTH

AVATAR_VARIANTS = 6


@dataclass
class CommentNode:
    comment: Comment
    author: str
    initials: str
    variant: int
    created_at: datetime
    depth: int
    is_owner: bool
    is_pending: bool
    can_edit: bool
    can_report: bool
    voted: bool
    children: list[CommentNode] = field(default_factory=list)

    @property
    def reply_form_id(self) -> str:
        return f"reply-{self.comment.pk}"


def _initials(name: str) -> str:
    parts = [part for part in name.replace("_", " ").split() if part]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def visible_comments(post: Post, viewer) -> list[Comment]:
    """Approved comments, plus the viewer's own comments still awaiting review."""
    condition = Q(status=Comment.Status.APPROVED)
    if getattr(viewer, "is_authenticated", False):
        condition |= Q(user=viewer, status=Comment.Status.PENDING)
    return list(
        Comment.objects.filter(condition, post=post, is_deleted=False)
        .select_related("user")
        .order_by("created_at")
    )


def comment_tree(post: Post, viewer, now: datetime) -> list[CommentNode]:
    """Top-level comments newest first, replies oldest first, depth-limited."""
    comments = visible_comments(post, viewer)
    authenticated = getattr(viewer, "is_authenticated", False)
    voted: set[int] = set()
    if authenticated and comments:
        voted = set(
            CommentVote.objects.filter(
                user=viewer, comment_id__in=[c.pk for c in comments]
            ).values_list("comment_id", flat=True)
        )
    nodes: dict[int, CommentNode] = {}
    for comment in comments:
        name = comment.user.public_display_name
        is_owner = authenticated and comment.user_id == viewer.pk
        nodes[comment.pk] = CommentNode(
            comment=comment,
            author=name,
            initials=_initials(name),
            variant=comment.user_id % AVATAR_VARIANTS,
            created_at=comment.created_at,
            depth=1,
            is_owner=is_owner,
            is_pending=comment.status == Comment.Status.PENDING,
            can_edit=is_owner and now - comment.created_at <= EDIT_WINDOW,
            can_report=authenticated
            and not is_owner
            and comment.status == Comment.Status.APPROVED,
            voted=comment.pk in voted,
        )
    roots: list[CommentNode] = []
    for comment in comments:
        node = nodes[comment.pk]
        parent = nodes.get(comment.parent_id) if comment.parent_id else None
        if parent is None:
            if comment.parent_id is None:
                roots.append(node)
            continue
        node.depth = min(parent.depth + 1, MAX_THREAD_DEPTH)
        parent.children.append(node)
    roots.reverse()
    return roots
