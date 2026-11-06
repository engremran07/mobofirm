"""Tag services: automation, deterministic suggestions and generic tagging."""

from apps.tags.services.automation import (
    assign_catalog_tags,
    curate_suggested_tags,
    ensure_curated_tag,
    merge_tags,
    refresh_usage_counts,
    sync_post_tags,
)
from apps.tags.services.heuristics import auto_tag_post, suggest_tags_from_text
from apps.tags.services.tagging_service import TaggingService

__all__ = [
    "TaggingService",
    "assign_catalog_tags",
    "auto_tag_post",
    "curate_suggested_tags",
    "ensure_curated_tag",
    "merge_tags",
    "refresh_usage_counts",
    "suggest_tags_from_text",
    "sync_post_tags",
]
