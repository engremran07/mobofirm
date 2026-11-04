from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdRequest:
    placement_code: str
    page_url: str
    referrer: str = ""
    user_id: int | None = None
    session_id: str | None = None
    consent_ads: bool = False
    device: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreativeSelection:
    creative_id: int
    campaign_id: int | None
    weight: int
    reason: str = ""


@dataclass
class AdResponse:
    placement_code: str
    creatives: list[CreativeSelection]
    tracking: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False
