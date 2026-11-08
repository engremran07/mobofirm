"""Publication side effects owned by the blog: wallet rewards and the bus event.

Runs once per publication (after commit, from ``signals``). Authors earn the
``blog.*`` reward events only for posts a *different* staff member published;
automation, AI-generated and self-published posts earn nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from django.apps import apps

from apps.blog.models import Post
from apps.core.app_service import app_enabled
from apps.core.events.bus import event_bus

logger = logging.getLogger(__name__)

POST_PUBLISHED_EVENT: Final = "blog.post_published"


def _wallet_ready() -> bool:
    return apps.is_installed("apps.wallet") and app_enabled("wallet")


def _earns_rewards(post: Post) -> bool:
    if post.is_ai_generated or post.published_by_id in (None, post.author_id):
        return False
    from apps.blog.selectors import is_editorial

    return not is_editorial(post)


def grant_publication_rewards(post: Post) -> list[Any]:
    """Pay the author: the base event (or the guide event) plus the featured extra."""
    if not _wallet_ready() or not _earns_rewards(post):
        return []
    from apps.blog.reward_events import (
        GUIDE_PUBLISHED,
        POST_PUBLISHED,
        POST_PUBLISHED_FEATURED,
    )
    from apps.wallet.api import WalletError, grant_reward

    events = [GUIDE_PUBLISHED if post.guide_request_id else POST_PUBLISHED]
    if post.is_gated:
        events.append(POST_PUBLISHED_FEATURED)
    context = {"post_id": post.pk, "access": post.access}
    entries = []
    for event in events:
        try:
            entry = grant_reward(
                post.author,
                event,
                key=f"{event}:{post.pk}",
                reference=f"blog.post:{post.pk}",
                context=context,
            )
        except WalletError:
            logger.exception("Reward %s for post %s failed", event, post.pk)
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def announce_publication(post: Post) -> None:
    """Tell other apps (gamification, distribution) that a post went live."""
    event_bus.publish(
        POST_PUBLISHED_EVENT,
        {
            "post_id": post.pk,
            "author_id": post.author_id,
            "author": post.author,
            "access": post.access,
            "guide_request_id": post.guide_request_id,
            "earns_rewards": _earns_rewards(post),
        },
    )
