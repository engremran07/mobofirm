"""Tag automation: create, assign and curate tags without manual steps.

Policy
------
* **Catalog tags** (brand, model, chipset, firmware category, post category) come from
  reviewed catalog facts, so they are created *curated* and attached immediately.
* **Text suggestions** start uncurated. A suggestion is promoted automatically once
  at least ``AUTO_CURATE_MIN_POSTS`` live posts mention it, then re-attached.
* Everything here is idempotent: running it twice leaves the same tags, links and
  counts, so it is safe to call from retried Celery tasks and post-commit hooks.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify

from apps.tags.models import Tag, TagsSettings
from apps.tags.services.heuristics import (
    MAX_NAME_LENGTH,
    curated_matches,
    get_or_create_suggestion,
    normalize,
    post_text,
    suggest_tags_from_text,
)

logger = logging.getLogger(__name__)

AUTO_CURATE_MIN_POSTS = 3
MAX_TAGS_PER_POST = 15
MAX_SUGGESTIONS_PER_POST = 5


def automation_enabled() -> bool:
    return TagsSettings.get_solo().enable_ai_suggestions


def ensure_curated_tag(name: str) -> Tag | None:
    """Return the curated, active tag for a trusted ``name``, creating it if needed."""
    name = " ".join((name or "").split())[:MAX_NAME_LENGTH]
    norm = normalize(name)
    slug = slugify(norm)[:80]
    if not norm or not slug:
        return None
    existing = Tag.objects.filter(Q(normalized_name=norm) | Q(slug=slug)).order_by(
        "is_deleted", "pk"
    )
    tag = existing.first()
    if tag is None:
        try:
            with transaction.atomic():
                tag = Tag.objects.create(
                    name=name,
                    normalized_name=norm,
                    slug=slug,
                    is_curated=True,
                    is_active=True,
                )
        except IntegrityError:
            tag = existing.first()
        if tag is None:
            return None
    seen: set[int] = set()
    while tag.merge_into_id is not None and tag.pk not in seen:
        seen.add(tag.pk)
        merged_into = tag.merge_into
        if merged_into is None:
            break
        tag = merged_into
    if tag.is_deleted:
        return None
    if not (tag.is_curated and tag.is_active):
        tag.is_curated = True
        tag.is_active = True
        tag.ai_suggested = False
        tag.save(
            update_fields=["is_curated", "is_active", "ai_suggested", "updated_at"]
        )
    return tag


def refresh_usage_counts(tag_ids: Iterable[int] | None = None) -> int:
    """Set ``usage_count`` to each tag's number of live posts; returns rows changed."""
    from apps.blog.models import Post

    live_posts = (
        Post.objects.live()
        .filter(tags=OuterRef("pk"))
        .order_by()
        .values("tags")
        .annotate(total=Count("pk"))
        .values("total")
    )
    tags = Tag.objects.all()
    if tag_ids is not None:
        tags = tags.filter(pk__in=list(tag_ids))
    return tags.update(usage_count=Coalesce(Subquery(live_posts), 0))


def assign_catalog_tags(post: Any, names: Iterable[str]) -> list[Tag]:
    """Make ``names`` (trusted catalog facts) the post's tag set, curated and attached."""
    wanted: dict[int, Tag] = {}
    for name in names:
        tag = ensure_curated_tag(name)
        if tag is not None:
            wanted[tag.pk] = tag
        if len(wanted) >= MAX_TAGS_PER_POST:
            break
    previous = set(post.tags.values_list("pk", flat=True))
    if previous != set(wanted):
        post.tags.set(list(wanted.values()))
        refresh_usage_counts(previous | set(wanted))
    return list(wanted.values())


def post_catalog_names(post: Any) -> list[str]:
    """Trusted tag names derivable from the post's own links and category."""
    names: list[str] = []
    brand = getattr(post, "firmware_brand", None)
    model = getattr(post, "firmware_model", None)
    if brand is not None:
        names.append(brand.name)
    if model is not None:
        names.append(f"{brand.name} {model.name}" if brand is not None else model.name)
        chipset = getattr(model, "chipset_ref", None)
        if chipset is not None:
            names.append(chipset.display_name)
    category = getattr(post, "category", None)
    if category is not None and category.slug != "chipsets":
        names.append(category.name)
    return names


@transaction.atomic
def sync_post_tags(post: Any) -> list[Tag]:
    """Attach catalog tags and matching curated tags to ``post``; queue new keywords.

    Existing tags are never removed, so editorial choices survive re-runs.
    """
    if not automation_enabled():
        return []
    attached: dict[int, Tag] = {}
    for name in post_catalog_names(post):
        tag = ensure_curated_tag(name)
        if tag is not None:
            attached[tag.pk] = tag
    text = post_text(post)
    for tag in curated_matches(text):
        attached[tag.pk] = tag
    for suggestion in suggest_tags_from_text(text, limit=MAX_SUGGESTIONS_PER_POST):
        tag = Tag.objects.filter(
            normalized_name=suggestion["normalized"],
            is_deleted=False,
            merge_into__isnull=True,
        ).first()
        if tag is None:
            get_or_create_suggestion(suggestion["name"])
    current = set(post.tags.values_list("pk", flat=True))
    additions = [tag for pk, tag in attached.items() if pk not in current]
    room = MAX_TAGS_PER_POST - len(current)
    added = additions[: max(room, 0)]
    if added:
        post.tags.add(*added)
    refresh_usage_counts(current | {tag.pk for tag in added})
    return list(attached.values())


def curate_suggested_tags(limit: int = 200) -> list[int]:
    """Promote suggestions mentioned by enough live posts; returns promoted tag ids."""
    from apps.blog.models import Post

    promoted: list[int] = []
    candidates = Tag.objects.filter(
        is_curated=False, is_deleted=False, merge_into__isnull=True, ai_suggested=True
    ).order_by("created_at")[:limit]
    for tag in candidates:
        mentions = (
            Post.objects.live()
            .filter(
                Q(title__icontains=tag.name)
                | Q(summary__icontains=tag.name)
                | Q(body__icontains=tag.name)
            )
            .count()
        )
        if mentions >= AUTO_CURATE_MIN_POSTS:
            tag.is_curated = True
            tag.is_active = True
            tag.ai_suggested = False
            tag.save(
                update_fields=["is_curated", "is_active", "ai_suggested", "updated_at"]
            )
            promoted.append(tag.pk)
    if promoted:
        logger.info("Auto-curated %d tags: %s", len(promoted), promoted)
    return promoted


def reattach_curated_tag(tag: Tag, limit: int = 500) -> int:
    """Attach a freshly curated ``tag`` to the live posts that mention it."""
    from apps.blog.models import Post

    posts = Post.objects.live().filter(
        Q(title__icontains=tag.name)
        | Q(summary__icontains=tag.name)
        | Q(body__icontains=tag.name)
    )[:limit]
    attached = 0
    for post in posts:
        if not post.tags.filter(pk=tag.pk).exists():
            post.tags.add(tag)
            attached += 1
    refresh_usage_counts([tag.pk])
    return attached


def merge_tags(source: Tag, target: Tag, user=None) -> None:
    """Atomically move every post from ``source`` to ``target`` and retire ``source``."""
    if source.pk == target.pk:
        raise ValueError("A tag cannot be merged into itself.")
    with transaction.atomic():
        for post in source.posts.all():
            post.tags.add(target)
            post.tags.remove(source)
        source.merge_into = target
        source.is_active = False
        source.deleted_at = timezone.now()
        source.deleted_by = user
        source.is_deleted = True
        source.save(
            update_fields=[
                "merge_into",
                "is_active",
                "is_deleted",
                "deleted_at",
                "deleted_by",
                "updated_at",
            ]
        )
        refresh_usage_counts([source.pk, target.pk])
