"""Topic-driven blog generation.

Flow: ``AutoTopic`` (queued) -> claim -> model call (no database transaction held)
-> sanitise and check -> save the draft with SEO fields and tags -> submit for staff
review. The model never publishes; a staff member does, in the blog workflow.

Idempotency: a topic keeps its ``post``, so a retry reuses the same draft instead of
creating a second one, and a ``succeeded`` topic returns its post unchanged.
Failure is closed: any provider or content problem marks the topic ``failed`` with
the reason and leaves no published content.
"""

from __future__ import annotations

import logging
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import slugify

from apps.blog.models import AutoTopic, Post, PostStatus
from apps.blog.rendering import plain_excerpt
from apps.blog.services.history import PostHistoryService
from apps.blog.services.workflow import request_review
from apps.core import ai_client
from apps.core.utils.sanitize import sanitize_html
from apps.tags.services import sync_post_tags

logger = logging.getLogger(__name__)

MIN_WORDS = 200
MAX_ERROR_LENGTH = 500
PROMPT = (
    "Write a concise, well-structured blog post as HTML. Use a short introduction, "
    "3-5 <h2> sections, bullet lists where helpful and a brief conclusion. Do not "
    "include <html>, <body> or <h1> tags, and do not mention AI or that this text "
    "was generated. Topic: {topic}"
)


class GenerationError(Exception):
    """The topic could not be turned into acceptable content."""


def _unique_slug(base: str) -> str:
    base = slugify(base)[:180] or "post"
    candidate, index = base, 2
    while Post.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def check_content(body_html: str) -> None:
    """Raise ``GenerationError`` unless ``body_html`` is publishable-quality."""
    text = strip_tags(body_html).strip()
    if len(text.split()) < MIN_WORDS:
        raise GenerationError("Generated content is too short.")
    lower = text.lower()
    if "as an ai" in lower or "language model" in lower:
        raise GenerationError("Generated content discloses its own generation.")
    if "<h2" not in body_html.lower():
        raise GenerationError("Generated content has no section headings.")


def _claim(topic: AutoTopic) -> AutoTopic:
    """Mark the topic running; return it. Raises if it cannot be processed."""
    with transaction.atomic():
        locked = (
            AutoTopic.objects.select_for_update()
            .select_related("created_by", "post")
            .get(pk=topic.pk)
        )
        if locked.status == "running":
            raise RuntimeError("This topic is already being processed.")
        if locked.status not in {"queued", "failed", "succeeded"}:
            raise ValueError(f"Topic cannot be processed from status {locked.status}.")
        if locked.status == "succeeded":
            return locked
        locked.status = "running"
        locked.retry_count += 1
        locked.last_attempt_at = timezone.now()
        locked.save(
            update_fields=["status", "retry_count", "last_attempt_at", "updated_at"]
        )
        return locked


def _fail(topic: AutoTopic, reason: str) -> None:
    AutoTopic.objects.filter(pk=topic.pk).update(
        status="failed",
        last_error=reason[:MAX_ERROR_LENGTH],
        updated_at=timezone.now(),
    )
    logger.warning("Topic %s failed: %s", topic.pk, reason)


def pick_author():
    from apps.users.models import CustomUser

    return (
        CustomUser.objects.filter(is_staff=True, is_active=True).order_by("pk").first()
    )


def generate_post_from_topic(
    topic: AutoTopic, *, submit_for_review: bool = True
) -> Post:
    """Generate (or reuse) the draft for ``topic`` and submit it for review."""
    topic = _claim(topic)
    if topic.status == "succeeded":
        if topic.post is None:
            raise ValueError("A completed topic has no associated post.")
        return topic.post
    author = topic.created_by or pick_author()
    if author is None:
        _fail(topic, "No staff author is available.")
        raise GenerationError("No staff author is available.")
    if topic.post is not None and topic.post.status != PostStatus.DRAFT:
        _fail(topic, "The generated post has already left draft.")
        raise GenerationError("The generated post has already left draft.")

    run_id = str(uuid.uuid4())
    try:
        raw = ai_client.generate_answer(
            question=PROMPT.format(topic=topic.topic), user=author
        )
        body_html = sanitize_html(raw).strip()
        check_content(body_html)
    except (ai_client.AiClientError, GenerationError) as error:
        _fail(topic, str(error))
        raise GenerationError(str(error)) from error

    summary = plain_excerpt(body_html, 300)
    try:
        post = _save_draft(topic, author, body_html, summary, run_id, submit_for_review)
    except (ValidationError, PermissionDenied) as error:
        message = " ".join(getattr(error, "messages", [str(error)]))
        _fail(topic, message)
        raise GenerationError(message) from error
    return post


def _save_draft(
    topic: AutoTopic,
    author,
    body_html: str,
    summary: str,
    run_id: str,
    submit_for_review: bool,
) -> Post:
    with transaction.atomic():
        post = topic.post
        if post is None:
            post = Post(
                title=topic.topic[:200],
                slug=_unique_slug(topic.topic),
                author=author,
                status=PostStatus.DRAFT,
                is_ai_generated=True,
                allow_comments=True,
            )
        post.body = body_html
        post.summary = summary[:1000]
        post.seo_title = topic.topic[:240]
        post.seo_description = plain_excerpt(body_html, 160)
        post.noindex = True
        post.ai_run_id = run_id
        post.ai_error = ""
        post.save()
        PostHistoryService.record_current_version(
            post, user=author, change_summary="AI-generated draft"
        )
        sync_post_tags(post)
        if submit_for_review:
            request_review(post, user=author)
        topic.post = post
        topic.ai_run_id = run_id
        topic.status = "succeeded"
        topic.last_error = ""
        topic.save(
            update_fields=["post", "ai_run_id", "status", "last_error", "updated_at"]
        )
    return post


def autoplan_topics(seed: str, count: int = 5) -> list[AutoTopic]:
    """Ask the model for topic ideas about ``seed`` and queue the new ones."""
    prompt = (
        f"List {count} concise blog post ideas, one per line, about: {seed}. "
        "Keep each under 120 characters. No numbering."
    )
    try:
        raw = ai_client.generate_answer(question=prompt, user=None)
    except ai_client.AiClientError as error:
        logger.warning("autoplan_topics failed: %s", error)
        return []
    queued: list[AutoTopic] = []
    for line in raw.splitlines():
        idea = line.strip("-•*0123456789. \t")[:240]
        if idea and not AutoTopic.objects.filter(topic__iexact=idea).exists():
            queued.append(AutoTopic.objects.create(topic=idea, status="queued"))
        if len(queued) >= count:
            break
    return queued
