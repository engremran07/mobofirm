"""Lazy seam to the bounty contract (``apps/bounty/api.py``) for guide requests.

Bounty owns escrow; blog owns requests. Blog reaches bounty only through
``AppService.get("bounty")`` and simply shows no bounty UI when it is absent, off or
paused (no wallet).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.urls import NoReverseMatch, reverse

from apps.core.app_service import AppService

KIND = "guide"


class GuideBounty(Protocol):
    """Fields the blog needs from the bounty app's public summary."""

    title: str
    label: str
    url: str
    credits: int
    bounty_count: int


@dataclass(frozen=True, slots=True)
class BountyOffer:
    """What the request page shows about the credits offered for a request."""

    available: bool
    count: int = 0
    credits: int = 0
    new_url: str = ""
    paused_reason: str = ""


NONE = BountyOffer(available=False)


def offer_for(request_id: int) -> BountyOffer:
    module = AppService.get("bounty")
    if module is None:
        return NONE
    ok, reason = module.availability(KIND)
    count, total_credits = module.bounty_summary(KIND, request_id)
    try:
        new_url = f"{reverse('bounty:create')}?{KIND}={request_id}"
    except NoReverseMatch:
        return NONE
    return BountyOffer(
        available=bool(ok),
        count=int(count),
        credits=int(total_credits),
        new_url=new_url,
        paused_reason=str(reason),
    )


def open_guide_bounties(limit: int = 5) -> list[GuideBounty]:
    """Public, bounded guide bounty summaries from the owning bounty contract."""
    module = AppService.get("bounty")
    if module is None:
        return []
    return module.open_guide_bounties(limit)
