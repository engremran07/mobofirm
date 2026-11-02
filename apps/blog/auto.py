from __future__ import annotations

import logging
import uuid

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core import ai_client
from apps.core.utils.sanitize import sanitize_html
from apps.core.views import _get_site_settings_snapshot
from apps.seo.models import Metadata, SEOModel
from apps.tags import services as tag_services
from apps.tags.models import Tag

from .models import AutoTopic, Post, PostStatus

logger = logging.getLogger(__name__)


def _unique_slug(base: str) -> str:
    base = slugify(base)[:180] or "post"
    candidate = base
    idx = 1
    while Post.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{idx}"
        idx += 1
    return candidate


def _sanitize_body(html: str) -> str:
    cleaned = sanitize_html(html or "")
    return cleaned.strip()


def _safety_checks(content: str, user) -> tuple[bool, str]:
    """
    Run basic safety validations for AI-generated copy.
    """
    text = (content or "").strip()
    if len(text.split()) < 200:
        return False, "Generated content too short for publication."

    lower = text.lower()
    if "as an ai" in lower or "as ai" in lower:
        return False, "AI disclosure text detected."

    try:
        moderation = ai_client.moderate_text(text, user or None)
        toxicity = float(moderation.get("toxicity_score") or 0.0)
        label = str(moderation.get("label") or "").lower()
        if toxicity >= 0.4 or label == "high":
            return False, f"Content blocked by moderation (toxicity={toxicity:.2f})."
    except Exception as exc:
        return False, f"Moderation unavailable: {exc}"

    return True, ""


def _sync_tag_usage(tags_qs):
    for tag in tags_qs:
        try:
            count = tag.posts.filter(
                status=PostStatus.PUBLISHED, publish_at__lte=timezone.now()
            ).count()
            if tag.usage_count != count:
                tag.usage_count = count
                tag.save(update_fields=["usage_count"])
        except Exception:  # noqa: S112
            continue


def _apply_seo(post: Post, content: str) -> None:
    """Generate SEO fields via AI with safe fallbacks."""
    try:
        title = ai_client.generate_title(content, post.author)
    except Exception:
        title = post.title[:240]
    try:
        summary = ai_client.generate_excerpt(content, post.author)
    except Exception:
        summary = post.summary or content[:300]
    try:
        desc = ai_client.generate_seo_description(content, post.author)
    except Exception:
        desc = summary[:320]

    post.seo_title = title[:240]
    post.seo_description = desc[:320]
    if not post.summary:
        post.summary = summary[:1000]

    canonical_url = post.canonical_url
    if not canonical_url:
        try:
            settings_snapshot = _get_site_settings_snapshot()
            host = (
                settings_snapshot.get("site_url")
                or settings_snapshot.get("site_domain")
                or ""
            )
            if host:
                canonical_url = host.rstrip("/") + post.get_absolute_url()
        except Exception:
            canonical_url = ""

    try:
        ct = ContentType.objects.get_for_model(Post)
        seo_obj, _ = SEOModel.objects.get_or_create(content_type=ct, object_id=post.pk)
        meta, _ = Metadata.objects.get_or_create(seo=seo_obj)
        meta.meta_title = post.seo_title
        meta.meta_description = post.seo_description
        meta.canonical_url = (
            canonical_url or meta.canonical_url or post.get_absolute_url()
        )
        meta.noindex = getattr(post, "noindex", False)
        meta.save()
    except Exception:
        logger.debug("SEO metadata update skipped", exc_info=True)


def _assign_tags(post: Post, content: str) -> None:
    try:
        suggested = ai_client.suggest_tags(content, post.author) or []
    except Exception:
        suggested = []
    for name in suggested[:5]:
        norm_slug = slugify(name)[:80] or "tag"
        norm_name = (name or "").strip()[:64]
        existing = (
            Tag.objects.filter(slug=norm_slug).first()
            or Tag.objects.filter(name__iexact=norm_name).first()
        )
        if not existing:
            try:
                existing = Tag.objects.create(
                    name=norm_name,
                    normalized_name=norm_slug[:64],
                    slug=norm_slug,
                    ai_suggested=True,
                    is_curated=False,
                )
            except Exception:
                existing = Tag.objects.filter(slug=norm_slug).first()
    if suggested:
        tags = list(Tag.objects.filter(name__in=suggested[:5]))
        if tags:
            post.tags.set(tags)
    if not post.tags.exists():
        tag_services.auto_tag_post(post, allow_create=True, max_tags=5)  # type: ignore[attr-defined]
    _sync_tag_usage(post.tags.all())


def _pick_author():
    try:
        from apps.users.models import CustomUser

        staff = CustomUser.objects.filter(is_staff=True).first()
        if staff:
            return staff
        return CustomUser.objects.first()
    except Exception:
        return None


def generate_post_from_topic(topic: AutoTopic, autopublish: bool = False) -> Post:
    """
    Generate a post for a queued topic.
    - If AI succeeds and autopublish=True -> publish immediately.
    - If AI fails or is incomplete -> leave as draft for review.
    """
    if topic.status not in {"queued", "failed"}:
        return topic.post

    now_ts = timezone.now()
    run_id = str(uuid.uuid4())

    with transaction.atomic():
        topic.status = "running"
        topic.retry_count = (topic.retry_count or 0) + 1
        topic.last_attempt_at = now_ts
        topic.save(
            update_fields=["status", "retry_count", "last_attempt_at", "updated_at"]
        )

        author = topic.created_by or _pick_author()
        if author is None:
            raise ValueError("No author available for AI blog generation.")
        title = topic.topic[:200]
        slug = _unique_slug(title)

        post = Post.objects.create(
            title=title,
            slug=slug,
            summary="",
            body="",
            author=author,
            status=PostStatus.DRAFT,
            publish_at=None,
            is_published=False,
            is_ai_generated=True,
            ai_run_id=run_id,
        )

        ai_ok = False
        ai_error = ""
        body_html = ""
        try:
            prompt = (
                "Write a concise, well-structured blog post in HTML. "
                "Include a short intro, 3-5 subheadings, bullet lists where helpful, and a brief conclusion. "
                "Avoid any mention of AI or being generated. Topic: "
                f"{topic.topic}."
            )
            body_html = ai_client.generate_answer(question=prompt, user=author or None)  # type: ignore[arg-type]
            body_html = _sanitize_body(body_html)
            ai_ok = bool(body_html)
        except Exception as exc:
            ai_error = str(exc)[:500]
            ai_ok = False

        if body_html:
            post.body = body_html
            try:
                post.summary = ai_client.generate_excerpt(body_html, author or None)[
                    :1000
                ]
            except Exception:
                post.summary = (body_html[:300] or topic.topic)[:1000]
            safety_ok, safety_error = _safety_checks(body_html, author)
            if not safety_ok:
                ai_ok = False
                ai_error = safety_error or ai_error

        post.ai_error = ai_error
        post.allow_comments = True
        post.noindex = not (ai_ok and autopublish)

        if ai_ok and autopublish:
            post.status = PostStatus.PUBLISHED
            post.publish_at = now_ts
            post.published_at = now_ts
            post.is_published = True
        else:
            post.status = PostStatus.DRAFT
            post.is_published = False

        post.save()
        _assign_tags(post, body_html or topic.topic)
        _apply_seo(post, body_html or topic.topic)
        reconcile_settings_with_post(post)

        topic.post = post
        topic.ai_run_id = post.ai_run_id or run_id
        topic.status = "succeeded" if ai_ok else "failed"
        topic.last_error = ai_error
        topic.save(
            update_fields=["post", "ai_run_id", "status", "last_error", "updated_at"]
        )

        return post


def autoplan_topics(seed: str, count: int = 5) -> list[AutoTopic]:
    """Generate topic ideas via AI and queue them."""
    ideas: list[str] = []
    try:
        prompt = (
            f"List {count} concise blog post ideas (one per line) about: {seed}. "
            "Keep each under 120 characters."
        )
        raw = ai_client.generate_answer(question=prompt, user=None)
        for line in raw.splitlines():
            line = line.strip("- •\t ")  # noqa: B005
            if not line:
                continue
            ideas.append(line[:240])
    except Exception as exc:
        logger.warning("autoplan_topics failed: %s", exc)

    queued: list[AutoTopic] = []
    for idea in ideas:
        queued.append(AutoTopic.objects.create(topic=idea, status="queued"))
    return queued


def reconcile_settings_with_post(post: Post) -> None:
    """Align tags/comments/seo flags with current settings (best effort)."""
    dirty_fields = []
    try:
        settings_snapshot = _get_site_settings_snapshot()
        if hasattr(post, "allow_comments") and not settings_snapshot.get(
            "enable_blog_comments", True
        ):
            if getattr(post, "allow_comments", True):
                post.allow_comments = False
                dirty_fields.append("allow_comments")
        if hasattr(post, "noindex") and post.is_ai_generated and not post.is_published:
            if not post.noindex:
                post.noindex = True
                dirty_fields.append("noindex")
    except Exception:  # noqa: S110
        pass
    try:
        if not post.tags.exists():
            tag_services.auto_tag_post(post, allow_create=True, max_tags=5)  # type: ignore[attr-defined]
        _sync_tag_usage(post.tags.all())
    except Exception:  # noqa: S110
        pass
    if dirty_fields:
        post.save(update_fields=dirty_fields)
