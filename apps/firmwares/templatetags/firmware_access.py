"""Template seam: who may change or request the access type of a firmware page."""

from __future__ import annotations

from typing import Any

from django import template

from .. import access_requests

register = template.Library()


@register.inclusion_tag("firmwares/includes/access_actions.html", takes_context=True)
def firmware_access_actions(
    context: dict[str, Any], firmware: Any, firmware_type: str
) -> dict[str, Any]:
    """Buttons for staff (change) or the uploader (request); empty for others."""
    user = context["request"].user
    if not user.is_authenticated or not firmware.is_verified:
        return {}
    meta = type(firmware)._meta
    can_change = bool(
        user.is_staff and user.has_perm(f"{meta.app_label}.change_{meta.model_name}")
    )
    can_request = access_requests.can_request(user, firmware)
    if not (can_change or can_request):
        return {}
    pending = access_requests.pending_request(firmware)
    return {
        "firmware": firmware,
        "firmware_type": firmware_type,
        "can_change": can_change,
        "can_request": can_request and not can_change,
        "pending": pending,
    }
