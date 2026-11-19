"""Registries other apps fill to join the credits economy.

An app declares what it pays (``RewardEvent``) in ``apps/<app>/reward_events.py`` and
what it charges (``SpendSink``) in ``apps/<app>/spend_sinks.py``; the wallet
autodiscovers both modules in ``ready()``. Amounts live only in the admin-editable
``RewardRule``/``CreditPrice`` rows, which are created from these defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import AbstractBaseUser


@dataclass(frozen=True)
class RewardEvent:
    """A thing a user can earn credits for."""

    key: str
    label: str
    app: str
    default_credits: int = 0
    default_daily_cap: int = 0
    default_monthly_cap: int = 0
    requires_verified_email: bool = True
    per_object_once: bool = True
    default_enabled: bool = True
    dynamic_amount: bool = False


@dataclass(frozen=True)
class SpendSink:
    """A thing credits can be spent on."""

    key: str
    label: str
    app: str
    default_credits: int = 0
    derived: bool = False
    """True when the owner derives the price (dollars × rate); staff do not edit it."""
    user_price: Callable[[Any], int | None] | None = field(default=None, compare=False)
    """Optional ``(user) -> credits`` for sinks whose price differs per account (a
    package override); ``None`` means "the sink price applies". Shown on the Ways to
    spend page."""


# (user, event_key, credits, context) -> adjusted credits
RewardModifier = Callable[[AbstractBaseUser, str, int, Mapping[str, Any]], int]
# (sink_key, credits, quantity, context) -> adjusted total credits
PriceModifier = Callable[[str, int, int, Mapping[str, Any]], int]

_reward_events: dict[str, RewardEvent] = {}
_spend_sinks: dict[str, SpendSink] = {}
_reward_modifiers: list[RewardModifier] = []
_price_modifiers: list[PriceModifier] = []


def register_reward_event(event: RewardEvent) -> RewardEvent:
    """Register (or idempotently re-register) an earn event; conflicting reuse fails."""
    existing = _reward_events.get(event.key)
    if existing is not None and existing != event:
        raise ValueError(f"Reward event {event.key!r} is already registered.")
    _reward_events[event.key] = event
    return event


def register_spend_sink(sink: SpendSink) -> SpendSink:
    """Register (or idempotently re-register) a spend sink; conflicting reuse fails."""
    existing = _spend_sinks.get(sink.key)
    if existing is not None and existing != sink:
        raise ValueError(f"Spend sink {sink.key!r} is already registered.")
    _spend_sinks[sink.key] = sink
    return sink


def register_reward_modifier(modifier: RewardModifier) -> RewardModifier:
    """Campaign hook: return the credits to grant for a reward (multiplier, bonus)."""
    if modifier not in _reward_modifiers:
        _reward_modifiers.append(modifier)
    return modifier


def register_price_modifier(modifier: PriceModifier) -> PriceModifier:
    """Campaign hook: return the credits to charge (discount)."""
    if modifier not in _price_modifiers:
        _price_modifiers.append(modifier)
    return modifier


def unregister_reward_modifier(modifier: RewardModifier) -> None:
    """End a reward campaign hook (a no-op when it was never registered)."""
    if modifier in _reward_modifiers:
        _reward_modifiers.remove(modifier)


def unregister_price_modifier(modifier: PriceModifier) -> None:
    """End a price campaign hook (a no-op when it was never registered)."""
    if modifier in _price_modifiers:
        _price_modifiers.remove(modifier)


def reward_events() -> Mapping[str, RewardEvent]:
    """A copy of every registered reward event, by key."""
    return dict(_reward_events)


def spend_sinks() -> Mapping[str, SpendSink]:
    """A copy of every registered spend sink, by key."""
    return dict(_spend_sinks)


def reward_modifiers() -> tuple[RewardModifier, ...]:
    """The registered reward campaign hooks, in registration order."""
    return tuple(_reward_modifiers)


def price_modifiers() -> tuple[PriceModifier, ...]:
    """The registered price campaign hooks, in registration order."""
    return tuple(_price_modifiers)
