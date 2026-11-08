"""Referral reward events declared to the wallet economy (amounts are wallet rules)."""

from __future__ import annotations

from apps.wallet.api import RewardEvent, register_reward_event

register_reward_event(
    RewardEvent(
        key="referral.verified",
        label="A friend you invited joined and contributed",
        app="referral",
        default_credits=20,
        default_monthly_cap=1000,
    )
)
register_reward_event(
    RewardEvent(
        key="referral.welcome",
        label="Welcome bonus for joining through an invitation",
        app="referral",
        default_credits=5,
    )
)
