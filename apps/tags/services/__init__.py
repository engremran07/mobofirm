"""Service layer initialization"""

from apps.tags.services.heuristics import auto_tag_post, suggest_tags_from_text
from apps.tags.services.tag_service import TagService
from apps.tags.services.tagging_service import TaggingService

__all__ = [
    "TagService",
    "TaggingService",
    "auto_tag_post",
    "suggest_tags_from_text",
]
