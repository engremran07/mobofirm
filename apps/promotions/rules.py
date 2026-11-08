"""Typed, validated earn rules: ``N events within D days`` earn the promotion's reward.

Stored as JSON on :class:`~apps.promotions.models.Promotion.earn_rules`::

    {"events": ["contribution.approved"], "count": 3, "within_days": 30}

``events`` are wallet reward event keys (counted when the wallet actually credits the
reward, so caps and anti-abuse already applied) or ``contribution.approved`` (an
approved contribution, counted once per approved object). ``within_days`` of 0 means
no time limit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

CONTRIBUTION_APPROVED: Final = "contribution.approved"
MAX_COUNT: Final = 1000
MAX_WITHIN_DAYS: Final = 365
MAX_EVENTS: Final = 10
MAX_EVENT_KEY_LENGTH: Final = 80
_ALLOWED_KEYS: Final = frozenset({"events", "count", "within_days"})


class EarnRuleError(ValueError):
    """The earn rules JSON does not follow the documented shape."""


@dataclass(frozen=True)
class EarnRules:
    events: tuple[str, ...]
    count: int
    within_days: int

    def describe(self, noun: str = "qualifying actions") -> str:
        window = f" within {self.within_days} days" if self.within_days else ""
        return f"{self.count} {noun}{window}"

    def as_json(self) -> dict[str, Any]:
        return {
            "events": list(self.events),
            "count": self.count,
            "within_days": self.within_days,
        }


def _whole_number(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EarnRuleError(f"{label} must be a whole number.")
    if not minimum <= value <= maximum:
        raise EarnRuleError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _event_keys(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise EarnRuleError("events must be a non-empty list of event keys.")
    if len(raw) > MAX_EVENTS:
        raise EarnRuleError(f"events may list at most {MAX_EVENTS} keys.")
    keys: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise EarnRuleError("Every event key must be a non-empty string.")
        key = item.strip()
        if len(key) > MAX_EVENT_KEY_LENGTH:
            raise EarnRuleError("An event key is too long.")
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def parse_earn_rules(raw: object) -> EarnRules:
    """Validate ``raw`` and return the typed rules, or raise :class:`EarnRuleError`."""
    if not isinstance(raw, Mapping):
        raise EarnRuleError("Earn rules must be a JSON object.")
    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise EarnRuleError(f"Unknown earn rule keys: {', '.join(sorted(unknown))}.")
    missing = {"events", "count"} - set(raw)
    if missing:
        raise EarnRuleError(f"Earn rules need: {', '.join(sorted(missing))}.")
    return EarnRules(
        events=_event_keys(raw["events"]),
        count=_whole_number(raw["count"], "count", minimum=1, maximum=MAX_COUNT),
        within_days=_whole_number(
            raw.get("within_days", 0),
            "within_days",
            minimum=0,
            maximum=MAX_WITHIN_DAYS,
        ),
    )
