"""Reader comment seeding through the comments service (automatic moderation)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.management.base import CommandError

from apps.blog.models import Post
from apps.comments.models import Comment
from apps.comments.services import submit_comment
from apps.core.seed_data.comments import COMMENTS, CommentSeed
from apps.core.seeding import SeedReport


def _post_for(seed: CommentSeed) -> Post | None:
    if seed.article:
        post = Post.objects.filter(slug=seed.article).first()
        if post is None:
            raise CommandError(
                f"Article {seed.article!r} does not exist; run seed_editorial first."
            )
        return post
    return Post.objects.filter(
        firmware_model__brand__slug=seed.brand,
        firmware_model__slug=seed.model,
        is_ai_generated=True,
    ).first()


def seed_comments(
    resolve_user: Callable[[str], AbstractBaseUser],
    comments: Sequence[CommentSeed] = COMMENTS,
) -> SeedReport:
    """Submit each comment; the service decides approved, queued or rejected."""
    report = SeedReport()
    submitted: dict[str, Comment] = {}
    for seed in comments:
        post = _post_for(seed)
        if post is None:
            report.record("comments skipped (post not seeded)", created=False)
            continue
        user = resolve_user(seed.username)
        existing = Comment.objects.filter(
            post=post, user_id=user.pk, body=seed.body
        ).first()
        if existing is not None:
            submitted[seed.key] = existing
            report.record("comments", created=False)
            continue
        parent = submitted.get(seed.reply_to) if seed.reply_to else None
        result = submit_comment(
            post, user, seed.body, parent_id=parent.pk if parent else None
        )
        submitted[seed.key] = result.comment
        report.record("comments", created=True)
        report.record(f"comments {result.comment.status}", created=True)
    return report
