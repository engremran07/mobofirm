"""Declare what bounties pay in credits (wallet contract, item 35)."""

from __future__ import annotations

from apps.wallet.api import RewardEvent, register_reward_event

# The amount is the poster's escrow, not a rule: the wallet pays ``credits=escrow``
# through release_escrow, so the rule only enables/disables bounty payouts and
# carries the abuse caps.
register_reward_event(
    RewardEvent(
        key="bounty.fulfilled",
        label="Fulfil a bounty",
        app="bounty",
        default_credits=0,
        dynamic_amount=True,
    )
)
