from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.core import ai_client
from apps.tags.models import Tag
from apps.tags.models_keyword import KeywordProvider, KeywordSuggestion


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().strip().split())


def suggest_tags_from_text(text: str, limit: int = 10) -> list[dict[str, str]]:
    text = (text or "").strip()
    if not text:
        return []
    # Basic heuristic seeds
    seeds = []
    try:
        ai_tags = ai_client.suggest_tags(text, None)
        seeds.extend(ai_tags or [])
    except Exception:  # noqa: S110
        pass
    # Deduplicate and normalize
    seen = set()
    out = []
    for name in seeds:
        norm = _normalize(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append({"name": name.strip(), "normalized": norm, "score": 0.6})
        if len(out) >= limit:
            break
    return out


def merge_tags(source: Tag, target: Tag, user=None) -> None:
    if source.pk == target.pk:
        return
    with transaction.atomic():
        # Reassign many-to-many relations from source to target (posts)
        posts_qs = getattr(source, "posts", None)
        if posts_qs is not None:
            for post in posts_qs.all():
                post.tags.add(target)
                post.tags.remove(source)
        source.merge_into = target
        source.is_active = False
        source.deleted_at = timezone.now()
        source.deleted_by = user
        source.save(
            update_fields=["merge_into", "is_active", "deleted_at", "deleted_by"]
        )
        # Update target usage
        target.usage_count = getattr(target, "posts", Tag.objects.none()).count()
        target.save(update_fields=["usage_count"])


def rebuild_usage() -> None:
    for tag in Tag.objects.all():
        try:
            tag.usage_count = getattr(tag, "posts", Tag.objects.none()).count()
            tag.save(update_fields=["usage_count"])
        except Exception:  # noqa: S112
            continue


def compute_content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def store_suggestions(tag: Tag, suggestions: list[dict], content_hash: str) -> None:
    tag.suggestions = suggestions
    tag.content_hash = content_hash
    tag.last_suggested_at = timezone.now()
    tag.save(update_fields=["suggestions", "content_hash", "last_suggested_at"])


def jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def fetch_external_keywords(provider: KeywordProvider) -> list[dict[str, str]]:
    """
    Stub for external keyword fetch; replace with real API calls per provider.config.
    Stores suggestions and returns normalized list for review.
    """
    if not provider.is_enabled:
        return []
    # Placeholder: no real API call without provider details.
    # In real integration, use provider.config / api_key to fetch data.
    return []


def store_keyword_suggestions(
    provider: KeywordProvider, keywords: list[dict[str, str]]
) -> None:
    now = timezone.now()
    for kw in keywords:
        norm = _normalize(kw.get("keyword", ""))
        if not norm:
            continue
        KeywordSuggestion.objects.update_or_create(
            provider=provider,
            normalized=norm,
            defaults={
                "keyword": kw.get("keyword", norm),
                "score": kw.get("score", 0.0),
                "locale": kw.get("locale", ""),
                "category": kw.get("category", ""),
                "metadata": kw.get("metadata", {}),
            },
        )
    provider.last_run_at = now
    provider.last_status = "ok"
    provider.save(update_fields=["last_run_at", "last_status"])


def auto_tag_post(post, allow_create: bool = True, max_tags: int = 5) -> list[Tag]:
    """
    Heuristic auto-tagging: derive candidate tags from title/summary/body.
    Creates AI-suggested tags if allowed and attaches them to the post.
    """
    text = " ".join(
        [
            getattr(post, "title", "") or "",
            getattr(post, "summary", "") or "",
            (getattr(post, "body", "") or "")[:4000],  # cap to avoid over-processing
        ]
    )
    suggestions = suggest_tags_from_text(text, limit=max_tags * 2)
    attached: list[Tag] = []
    for s in suggestions:
        norm = s.get("normalized") or _normalize(s.get("name", ""))
        if not norm:
            continue
        existing = (
            Tag.objects.filter(normalized_name=norm, is_deleted=False).first()
            or Tag.objects.filter(name__iexact=s.get("name", "")).first()
        )
        tag_obj = existing
        if not tag_obj and allow_create:
            tag_obj = Tag.objects.create(
                name=s.get("name", norm).strip()[:64],
                normalized_name=norm[:64],
                slug=slugify(norm)[:80],
                ai_suggested=True,
                is_curated=False,
            )
        if not tag_obj:
            continue
        post.tags.add(tag_obj)
        attached.append(tag_obj)
        if len(attached) >= max_tags:
            break
    # Update usage counts for attached tags
    for t in attached:
        try:
            t.usage_count = t.posts.count()  # type: ignore[attr-defined]
            t.save(update_fields=["usage_count"])
        except Exception:  # noqa: S112
            continue
    return attached
