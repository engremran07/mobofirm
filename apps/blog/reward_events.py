"""Credit rewards earned by writing for the blog (wallet contract, item 35).

Wallet autodiscovers this module. Amounts, caps and enablement are admin-editable
``RewardRule`` rows ("Reward rules"); the numbers below are only the defaults.

* ``blog.post_published`` - a member's post was approved and published by staff.
* ``blog.post_published.featured`` - the same for a featured or paid post (more).
* ``blog.guide_published`` - the approved post answered a guide request.
"""

from __future__ import annotations

from apps.wallet.api import RewardEvent, register_reward_event

POST_PUBLISHED = "blog.post_published"
POST_PUBLISHED_FEATURED = "blog.post_published.featured"
GUIDE_PUBLISHED = "blog.guide_published"

register_reward_event(
    RewardEvent(
        key=POST_PUBLISHED,
        label="Blog post published",
        app="blog",
        default_credits=10,
        default_daily_cap=50,
        default_monthly_cap=300,
    )
)
register_reward_event(
    RewardEvent(
        key=POST_PUBLISHED_FEATURED,
        label="Featured or paid blog post published",
        app="blog",
        default_credits=25,
        default_daily_cap=100,
        default_monthly_cap=600,
    )
)
register_reward_event(
    RewardEvent(
        key=GUIDE_PUBLISHED,
        label="Guide request answered",
        app="blog",
        default_credits=15,
        default_daily_cap=75,
        default_monthly_cap=450,
    )
)
