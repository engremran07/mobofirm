"""Declare what gamification pays in credits (wallet contract, item 35)."""

from __future__ import annotations

from apps.gamification.constants import BADGE_EARNED_REWARD_KEY
from apps.wallet.api import RewardEvent, register_reward_event

register_reward_event(
    RewardEvent(
        key=BADGE_EARNED_REWARD_KEY,
        label="Earn a badge",
        app="gamification",
        default_credits=0,
        dynamic_amount=True,
    )
)
