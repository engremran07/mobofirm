"""Blog signal wiring: publication side effects run once, after commit."""

from __future__ import annotations

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_save, pre_save

from apps.blog.models import Post, PostStatus


def _was_published(instance: Post) -> bool:
    if instance.pk is None:
        return False
    return Post.objects.filter(pk=instance.pk, status=PostStatus.PUBLISHED).exists()


def remember_previous_state(sender, instance: Post, **kwargs) -> None:
    instance._was_published = _was_published(instance)


def on_post_saved(sender, instance: Post, created: bool, **kwargs) -> None:
    """Fire publication hooks when a post *becomes* published, not on every save."""
    if instance.status != PostStatus.PUBLISHED:
        return
    if getattr(instance, "_was_published", False):
        return
    transaction.on_commit(lambda: _after_publish(instance.pk))


def _after_publish(post_id: int) -> None:
    post = Post.objects.filter(pk=post_id, status=PostStatus.PUBLISHED).first()
    if post is None:
        return
    from apps.tags.tasks import sync_post_tags

    sync_post_tags.delay(post.pk)

    if not post.is_ai_generated and apps.is_installed("apps.users"):
        from apps.blog.tasks import send_post_notifications_batched
        from apps.users.services.notifications import notifications_enabled

        if notifications_enabled():
            send_post_notifications_batched.delay(post.pk)

    if apps.is_installed("apps.ads"):
        from apps.ads.tasks import scan_blog_post_content

        scan_blog_post_content.delay(post.pk)

    from apps.core.signals import blog_post_published

    blog_post_published.send(sender=Post, post=post, user=post.author)


def connect_signals() -> None:
    pre_save.connect(
        remember_previous_state, sender=Post, dispatch_uid="blog_post_remember_state"
    )
    post_save.connect(on_post_saved, sender=Post, dispatch_uid="blog_post_published")
