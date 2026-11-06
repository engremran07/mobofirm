"""Template seam that shows a firmware record's storage locations to staff."""

from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled

from .. import services

register = template.Library()


@register.inclusion_tag("storage/_firmware_locations.html", takes_context=True)
def firmware_storage_panel(context: dict[str, Any], firmware: Any) -> dict[str, Any]:
    """Staff-only panel with the firmware's storage locations (empty for others)."""
    user = context["request"].user
    if not (user.is_authenticated and user.is_staff and app_enabled("storage")):
        return {}
    return {
        "locations": list(services.locations_for(firmware)),
        "status_url": f"{reverse('storage:status')}?q={firmware.pk}",
    }
