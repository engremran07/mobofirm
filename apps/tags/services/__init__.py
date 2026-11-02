"""Service layer initialization"""

from apps.tags.services.tag_service import TagService
from apps.tags.services.tagging_service import TaggingService

__all__ = ["TagService", "TaggingService"]
