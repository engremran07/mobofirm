"""Event subscribers."""

from __future__ import annotations

import logging
from typing import Any

from apps.bounty import services
from apps.core.events.bus import event_bus

logger = logging.getLogger(__name__)
_STATE = {"subscribed": False}


def on_firmware_approved(data: dict[str, Any]) -> None:
    """``firmware.approved`` (apps.firmwares.events): settle matching bounties."""
    services.handle_firmware_approved(data)


def on_guide_fulfilled(data: dict[str, Any]) -> None:
    """``blog.guide_fulfilled`` (apps.blog.guide_requests): pay the solution's author."""
    services.handle_guide_fulfilled(data)


def on_guide_request_closed(data: dict[str, Any]) -> None:
    """``blog.guide_request_closed``: refund bounties on a withdrawn request."""
    services.handle_guide_request_closed(data)


def register_handlers() -> None:
    """Subscribe once per process."""
    if not _STATE["subscribed"]:
        event_bus.subscribe("firmware.approved", on_firmware_approved)
        event_bus.subscribe("blog.guide_fulfilled", on_guide_fulfilled)
        event_bus.subscribe("blog.guide_request_closed", on_guide_request_closed)
        _STATE["subscribed"] = True
