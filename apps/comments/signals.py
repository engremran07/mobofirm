"""Keep ``Post.comments_count`` equal to the number of visible comments."""

from __future__ import annotations

from typing import Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.blog.models import Post
from apps.comments.models import Comment


def _sync_count(post_id: int | None) -> None:
    if post_id is None:
        return
    visible = Comment.objects.filter(
        post_id=post_id, status=Comment.Status.APPROVED, is_deleted=False
    ).count()
    Post.objects.filter(pk=post_id).exclude(comments_count=visible).update(
        comments_count=visible
    )


@receiver(post_save, sender=Comment, dispatch_uid="comments_sync_count_on_save")
def sync_count_on_save(instance: Comment, **kwargs: Any) -> None:
    _sync_count(instance.post_id)


@receiver(post_delete, sender=Comment, dispatch_uid="comments_sync_count_on_delete")
def sync_count_on_delete(instance: Comment, **kwargs: Any) -> None:
    _sync_count(instance.post_id)
