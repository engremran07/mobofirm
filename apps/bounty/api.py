"""Public service contract of bounty (other apps import only from here).

* ``bounty_summary(kind, object_id)`` - open bounty count and credits on a target
  (``kind``: request, model, variant or guide).
* ``availability(kind="request")`` - ``(ok, reason)``; bounties pause honestly without
  the wallet (or, for ``guide``, without the blog).
* Event bus: ``bounty.posted``, ``bounty.settled`` and ``bounty.closed``.
* Settlement hooks: bounty subscribes to ``firmware.approved`` (see
  ``apps.firmwares.events``), ``blog.guide_fulfilled`` (the answered request's
  bounties are paid to the post's author) and ``blog.guide_request_closed`` (refund);
  firmwares and blog never import bounty.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Sum
from django.utils import timezone

from apps.bounty.models import Bounty
from apps.bounty.services import availability, open_credits_for

MAX_PUBLIC_GUIDE_BOUNTIES = 10


@dataclass(frozen=True, slots=True)
class PublicGuideBounty:
    """Public summary of open escrow on an approved guide request."""

    title: str
    label: str
    url: str
    credits: int
    bounty_count: int


__all__ = [
    "PublicGuideBounty",
    "availability",
    "bounty_summary",
    "open_guide_bounties",
]


def bounty_summary(kind: str, object_id: int) -> tuple[int, int]:
    """``(bounty count, total credits)`` for ``kind`` in request/model/variant/guide."""
    return open_credits_for(kind, object_id)


def open_guide_bounties(limit: int = 5) -> list[PublicGuideBounty]:
    """Return bounded, unexpired escrow summaries for approved public guide requests."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (1 <= limit <= MAX_PUBLIC_GUIDE_BOUNTIES)
    ):
        raise ValueError(
            f"Guide bounty limit must be between 1 and {MAX_PUBLIC_GUIDE_BOUNTIES}."
        )
    if not availability("guide")[0]:
        return []

    guide_model = apps.get_model("blog", "guiderequest")
    approved_guides = guide_model.objects.filter(status="approved")
    content_type = ContentType.objects.get_for_model(guide_model)
    summaries = list(
        Bounty.objects.filter(
            target_type=content_type,
            target_id__in=approved_guides.values("pk"),
            status=Bounty.Status.OPEN,
            expires_at__gt=timezone.now(),
        )
        .values("target_id")
        .annotate(credits=Sum("credits"), bounty_count=Count("pk"))
        .order_by("-credits", "target_id")[:limit]
    )
    if not summaries:
        return []

    guides = guide_model.objects.filter(
        pk__in=[summary["target_id"] for summary in summaries],
        status="approved",
    ).select_related("brand", "firmware_model", "variant", "chipset")
    by_id = {guide.pk: guide for guide in guides}
    return [
        PublicGuideBounty(
            title=by_id[item["target_id"]].title,
            label=by_id[item["target_id"]].label,
            url=by_id[item["target_id"]].get_absolute_url(),
            credits=int(item["credits"] or 0),
            bounty_count=int(item["bounty_count"]),
        )
        for item in summaries
        if item["target_id"] in by_id
    ]
