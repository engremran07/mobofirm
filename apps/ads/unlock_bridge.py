"""Bridge between rewarded ads and the shop's file unlock tickets.

``apps.ads`` owns the watch (server-validated start, heartbeat and complete); the shop
owns what a watch counts toward. This module is the only place that knows both: it turns
the shop's :class:`~apps.shop.ad_unlock.UnlockRefused` into a ``RewardedAdError`` and
plugs the shop's ticket rules into the ``download_unlock`` reward type.
"""

from __future__ import annotations

from typing import Any

from apps.ads.models import RewardedAdConfig, RewardedAdView
from apps.ads.rewarded import RewardedAdError, RewardHandler, register_reward_handler
from apps.shop import ad_unlock


def _check(user: Any, reference: str) -> None:
    try:
        ad_unlock.check_watch(user, reference)
    except ad_unlock.UnlockRefused as refusal:
        raise RewardedAdError(
            refusal.code,
            refusal.message,
            status=refusal.status,
            retry_after=refusal.retry_after,
        ) from refusal


def _grant(view: RewardedAdView) -> tuple[bool, str]:
    outcome = ad_unlock.record_watch(view.user, view.reference, view.pk)
    return outcome.counted, outcome.message


def register() -> None:
    """Plug the shop's unlock rules into the ``download_unlock`` reward type."""
    register_reward_handler(
        RewardedAdConfig.RewardType.DOWNLOAD_UNLOCK,
        RewardHandler(check=_check, grant=_grant),
    )
