from __future__ import annotations

from typing import Final

KIND_WEBHOOK: Final = "webhook"
KIND_TELEGRAM: Final = "telegram"
KIND_DISCORD: Final = "discord"
KIND_X: Final = "x"
KIND_YOUTUBE: Final = "youtube"
KIND_FACEBOOK: Final = "facebook"
KIND_INSTAGRAM: Final = "instagram"
KIND_THREADS: Final = "threads"
KIND_LINKEDIN: Final = "linkedin"
KIND_REDDIT: Final = "reddit"
KIND_PINTEREST: Final = "pinterest"
KIND_TIKTOK: Final = "tiktok"
KIND_MASTODON: Final = "mastodon"
KIND_BLUESKY: Final = "bluesky"
KIND_WHATSAPP: Final = "whatsapp"
KIND_SLACK: Final = "slack"

CONTENT_FIRMWARE: Final = "firmware"
CONTENT_BLOG: Final = "blog"
CONTENT_TEST: Final = "test"
SHAREABLE_KINDS: Final = (CONTENT_FIRMWARE, CONTENT_BLOG)

DEFAULT_FIRMWARE_TEMPLATE: Final = "New firmware: {title}\n{url}"
DEFAULT_BLOG_TEMPLATE: Final = "New on {site}: {title}\n{url}"

TEMPLATE_FIELDS: Final = (
    "title",
    "url",
    "summary",
    "brand",
    "model",
    "variant",
    "kind",
    "site",
)

# Seconds to wait after attempt 1, 2, 3 ... (the last value repeats).
RETRY_BACKOFF: Final = (60, 300, 1800, 7200, 21600)
LEASE_SECONDS: Final = 300
CHANNEL_TRIP_AFTER: Final = 10

# Approval modes per channel.
APPROVAL_AUTO: Final = "auto"
APPROVAL_REQUIRED: Final = "approval"
APPROVAL_SCHEDULE_ONLY: Final = "schedule_only"

# Media a platform needs for a post.
MEDIA_TEXT: Final = "text"
MEDIA_IMAGE: Final = "image"
MEDIA_VIDEO: Final = "video"

# Hook-first copy styles used by the composer and the A/B selection.
HOOK_QUESTION: Final = "question"
HOOK_BENEFIT: Final = "benefit"
HOOK_URGENCY: Final = "urgency"
HOOK_HOWTO: Final = "howto"
HOOK_EVERGREEN: Final = "evergreen"
HOOK_PLAIN: Final = "plain"
DEFAULT_HOOKS: Final = (HOOK_QUESTION, HOOK_BENEFIT, HOOK_HOWTO, HOOK_URGENCY)

EMOJI_NONE: Final = "none"
EMOJI_MINIMAL: Final = "minimal"
EMOJI_EXPRESSIVE: Final = "expressive"

SHORT_CODE_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
SHORT_CODE_LENGTH: Final = 7

# Models launched within this many days are "trending" (launch boost).
TRENDING_DAYS: Final = 14
# A job waiting for media is dropped after this many days.
MEDIA_WAIT_DAYS: Final = 7
DEFAULT_MEDIA_RETENTION_DAYS: Final = 30
DEFAULT_CLICK_RETENTION_DAYS: Final = 180
DEFAULT_LOG_RETENTION_DAYS: Final = 365
HOUSEKEEPING_EVERY_SECONDS: Final = 3600
