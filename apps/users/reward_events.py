"""Reward events the users app declares to the wallet economy (amounts are wallet rules)."""

from __future__ import annotations

from apps.wallet.api import RewardEvent, register_reward_event

PROFILE_COMPLETED_EVENT = "profile.completed"

register_reward_event(
    RewardEvent(
        key=PROFILE_COMPLETED_EVENT,
        label="Completed your profile",
        app="users",
        default_credits=10,
    )
)
