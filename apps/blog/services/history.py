from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.blog.models import Post, PostRevision, PostStatus

_TAGS_UNSET = object()

VERSIONED_FIELDS = {
    "title",
    "slug",
    "summary",
    "body",
    "seo_title",
    "seo_description",
    "canonical_url",
    "hero_image",
    "category_id",
    "firmware_brand_id",
    "firmware_model_id",
    "featured",
    "allow_comments",
    "noindex",
}
RESTORABLE_FIELDS = VERSIONED_FIELDS


class PostHistoryService:
    @classmethod
    def snapshot(cls, post: Post) -> dict[str, Any]:
        return {
            "title": post.title,
            "slug": post.slug,
            "summary": post.summary,
            "body": post.body,
            "seo_title": post.seo_title,
            "seo_description": post.seo_description,
            "canonical_url": post.canonical_url,
            "hero_image": post.hero_image,
            "category_id": post.category_id,
            "firmware_brand_id": post.firmware_brand_id,
            "firmware_model_id": post.firmware_model_id,
            "featured": post.featured,
            "allow_comments": post.allow_comments,
            "noindex": post.noindex,
            "tags": list(post.tags.order_by("slug").values_list("slug", flat=True)),
            "status": post.status,
            "published_at": post.published_at.isoformat()
            if post.published_at
            else None,
            "version": post.version,
        }

    @classmethod
    def _record_current_locked(
        cls, post: Post, user, change_summary: str
    ) -> PostRevision:
        revisions = PostRevision.objects.filter(post=post).order_by(
            "-created_at", "-pk"
        )
        for revision in revisions:
            if revision.snapshot.get("version") == post.version:
                return revision

        snapshot = cls.snapshot(post)
        snapshot["change_summary"] = change_summary
        return PostRevision.objects.create(post=post, user=user, snapshot=snapshot)

    @classmethod
    @transaction.atomic
    def record_current_version(
        cls, post: Post, *, user=None, change_summary: str = "Initial version"
    ) -> PostRevision:
        locked = Post.objects.select_for_update().get(pk=post.pk)
        return cls._record_current_locked(locked, user, change_summary)

    @staticmethod
    def _field_map() -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for field in Post._meta.concrete_fields:
            fields[field.name] = field
            fields[field.attname] = field
        return fields

    @staticmethod
    def _normalized_value(field, value):
        if field.is_relation and value is not None and hasattr(value, "_meta"):
            return value.pk
        return value

    @classmethod
    def _validate_fields(cls, fields: dict[str, Any]) -> dict[str, Any]:
        field_map = cls._field_map()
        validated = {}
        for name, value in fields.items():
            field = field_map.get(name)
            if field is None or field.primary_key or field.name == "version":
                raise ValidationError(f"Unsupported post field: {name}.")
            validated[name] = value
        return validated

    @classmethod
    def _content_changed(
        cls,
        post: Post,
        fields: dict[str, Any],
        tags=_TAGS_UNSET,
    ) -> bool:
        field_map = cls._field_map()
        for name, value in fields.items():
            field = field_map[name]
            if field.attname in VERSIONED_FIELDS and getattr(
                post, field.attname
            ) != cls._normalized_value(field, value):
                return True

        if tags is not _TAGS_UNSET:
            desired_ids = {item.pk if hasattr(item, "_meta") else item for item in tags}
            current_ids = set(post.tags.values_list("pk", flat=True))
            if desired_ids != current_ids:
                return True
        return False

    @classmethod
    def _update_locked(
        cls,
        post: Post,
        *,
        user,
        change_summary: str,
        fields: dict[str, Any],
        tags=_TAGS_UNSET,
    ) -> Post:
        fields = cls._validate_fields(fields)
        content_changed = cls._content_changed(post, fields, tags)
        if content_changed:
            cls._record_current_locked(post, user, change_summary)

        for name, value in fields.items():
            setattr(post, name, value)
        if content_changed:
            post.version += 1
        if fields or content_changed:
            post.save()
        if tags is not _TAGS_UNSET:
            post.tags.set(tags)
        return post

    @classmethod
    @transaction.atomic
    def update_post(
        cls,
        post: Post,
        *,
        user=None,
        change_summary: str,
        tags=_TAGS_UNSET,
        **fields,
    ) -> Post:
        locked = Post.objects.select_for_update().get(pk=post.pk)
        cls._update_locked(
            locked,
            user=user,
            change_summary=change_summary,
            fields=fields,
            tags=tags,
        )
        post.refresh_from_db()
        return post

    @classmethod
    @transaction.atomic
    def restore_revision(cls, post: Post, revision: PostRevision, *, user=None) -> Post:
        locked = Post.objects.select_for_update().get(pk=post.pk)
        if revision.post_id != locked.pk:
            raise ValidationError("The selected revision does not belong to this post.")
        if locked.status != PostStatus.DRAFT:
            raise ValidationError("Only draft posts can be restored from history.")

        snapshot = revision.snapshot
        fields = {
            name: snapshot[name] for name in RESTORABLE_FIELDS if name in snapshot
        }
        tags = _TAGS_UNSET
        if "tags" in snapshot:
            slugs = snapshot["tags"]
            if not isinstance(slugs, list) or not all(
                isinstance(slug, str) for slug in slugs
            ):
                raise ValidationError("The saved tag list is invalid.")
            tag_model = locked.tags.model
            restored_tags = list(tag_model.objects.filter(slug__in=slugs))
            if {tag.slug for tag in restored_tags} != set(slugs):
                raise ValidationError("One or more saved tags are no longer available.")
            tags = restored_tags

        slug = fields.get("slug")
        if slug and Post.objects.filter(slug=slug).exclude(pk=locked.pk).exists():
            raise ValidationError("The saved slug is already in use.")

        return cls._update_locked(
            locked,
            user=user,
            change_summary=f"Restored from version {snapshot.get('version', 'unknown')}",
            fields=fields,
            tags=tags,
        )
