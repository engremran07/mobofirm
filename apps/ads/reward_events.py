"""Reward events the ads app declares to the wallet's economy engine.

The wallet autodiscovers this module. The amount and caps are the admin-editable
reward rule (Credits economy → Reward rules); these defaults only seed it.
"""

from __future__ import annotations

from django.apps import apps as django_apps

EVENT_KEY = "ads.rewarded_view_completed"

if django_apps.is_installed("apps.wallet"):
    from apps.wallet.api import RewardEvent, register_reward_event

    register_reward_event(
        RewardEvent(
            key=EVENT_KEY,
            label="Watch a rewarded ad",
            app="ads",
            default_credits=5,
            default_daily_cap=50,
            default_monthly_cap=500,
            requires_verified_email=True,
            per_object_once=True,
        )
    )
