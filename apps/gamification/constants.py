"""Event keys gamification understands and their admin-editable defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

FIRMWARE_APPROVED: Final = "firmware.approved"
REQUEST_FULFILLED: Final = "firmware.request_fulfilled"
COMMENT_APPROVED: Final = "comment.approved"
REFERRAL_REWARDED: Final = "referral.rewarded"
BOUNTY_FULFILLED: Final = "bounty.fulfilled"
BLOG_PUBLISHED: Final = "blog.post_published"

BADGE_EARNED_REWARD_KEY: Final = "gamification.badge_earned"


@dataclass(frozen=True, slots=True)
class XPEvent:
    key: str
    label: str
    default_xp: int
    default_daily_cap: int = 0


XP_EVENTS: Final[dict[str, XPEvent]] = {
    event.key: event
    for event in (
        XPEvent(FIRMWARE_APPROVED, "Firmware you uploaded was approved", 50),
        XPEvent(REQUEST_FULFILLED, "Your upload fulfilled a firmware request", 30),
        XPEvent(COMMENT_APPROVED, "A comment of yours was approved", 5, 10),
        XPEvent(REFERRAL_REWARDED, "A person you referred verified their account", 25),
        XPEvent(BOUNTY_FULFILLED, "You fulfilled a bounty", 75),
        XPEvent(BLOG_PUBLISHED, "An article or guide of yours was published", 40),
    )
}

# Badge metric -> how to read it (see services.metrics_for).
METRIC_XP_TOTAL: Final = "xp_total"
METRIC_STREAK: Final = "streak_days"
METRIC_EVENT_COUNTS: Final[dict[str, str]] = {
    "firmware_approved": FIRMWARE_APPROVED,
    "requests_fulfilled": REQUEST_FULFILLED,
    "comments_approved": COMMENT_APPROVED,
    "referrals_rewarded": REFERRAL_REWARDED,
    "bounties_fulfilled": BOUNTY_FULFILLED,
    "posts_published": BLOG_PUBLISHED,
}

DEFAULT_LEVELS: Final = (
    (1, "Newcomer", 0),
    (2, "Contributor", 100),
    (3, "Trusted", 500),
    (4, "Expert", 1500),
    (5, "Legend", 5000),
)

# (slug, name, description, icon, tier, metric, threshold, xp bonus)
DEFAULT_BADGES: Final = (
    (
        "first-upload",
        "First upload",
        "Your first firmware was approved.",
        "upload",
        "bronze",
        "firmware_approved",
        1,
        10,
    ),
    (
        "prolific-uploader",
        "Prolific uploader",
        "Ten approved firmware uploads.",
        "trophy",
        "gold",
        "firmware_approved",
        10,
        50,
    ),
    (
        "request-hunter",
        "Request hunter",
        "You fulfilled a firmware request.",
        "award",
        "silver",
        "requests_fulfilled",
        1,
        20,
    ),
    (
        "helpful-voice",
        "Helpful voice",
        "Five approved comments.",
        "message-circle",
        "bronze",
        "comments_approved",
        5,
        10,
    ),
    (
        "connector",
        "Connector",
        "A referred friend verified their account.",
        "gift",
        "silver",
        "referrals_rewarded",
        1,
        20,
    ),
    (
        "bounty-hunter",
        "Bounty hunter",
        "You fulfilled a bounty.",
        "medal",
        "gold",
        "bounties_fulfilled",
        1,
        50,
    ),
    (
        "week-streak",
        "Seven-day streak",
        "Active seven days in a row.",
        "flame",
        "silver",
        "streak_days",
        7,
        25,
    ),
    (
        "rising-star",
        "Rising star",
        "Reach 500 XP.",
        "star",
        "silver",
        "xp_total",
        500,
        0,
    ),
)
