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

from apps.bounty.services import availability, open_credits_for

__all__ = ["availability", "bounty_summary"]


def bounty_summary(kind: str, object_id: int) -> tuple[int, int]:
    """``(open bounties, total credits)`` for ``kind`` in request/model/variant/guide."""
    return open_credits_for(kind, object_id)
