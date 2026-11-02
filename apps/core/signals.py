from django.dispatch import Signal

blog_post_deleted = Signal()
blog_post_published = Signal()
content_viewed = Signal()
content_updated = Signal()
firmware_download_ready = Signal()
firmware_download_requested = Signal()
firmware_uploaded = Signal()
notification_requested = Signal()
storage_quota_exhausted = Signal()
