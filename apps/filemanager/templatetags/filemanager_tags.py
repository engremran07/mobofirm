from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.core.app_service import app_enabled

from .. import services

register = template.Library()

UNITS = ("B", "KB", "MB", "GB", "TB")


@register.filter(name="filesize")
def filesize(value: int | str | None) -> str:
    """Convert a byte count to a short human-readable string."""
    if value is None:
        return "—"
    try:
        size = float(int(value))
    except (TypeError, ValueError):
        return str(value)
    unit = 0
    while size >= 1024 and unit < len(UNITS) - 1:
        size /= 1024
        unit += 1
    return f"{int(size)} {UNITS[unit]}" if unit == 0 else f"{size:.1f} {UNITS[unit]}"


@register.inclusion_tag("components/dashboard_tile.html", takes_context=True)
def filemanager_dashboard_tile(context: dict[str, Any]) -> dict[str, Any]:
    """Account dashboard tile: storage used of the quota."""
    user = context["request"].user
    if not user.is_authenticated or not app_enabled("filemanager"):
        return {}
    usage = services.get_usage(user)
    return {
        "url": reverse("filemanager:browser"),
        "label": "File storage",
        "value": f"{usage.percent_used:.0f}% used",
        "icon": "folder",
        "hint": f"{filesize(usage.used_bytes)} of {filesize(usage.effective_quota)}",
    }
