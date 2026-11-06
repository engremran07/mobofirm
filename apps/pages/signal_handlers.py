"""Invalidate cached page widgets when the content they summarise changes."""

from __future__ import annotations

from django.db.models import Model
from django.db.models.signals import post_delete, post_save

from .widgets import invalidate_widget_caches

WIDGET_SOURCE_MODELS = (
    "blog.Post",
    "firmwares.OfficialFirmware",
    "firmwares.EngineeringFirmware",
    "firmwares.ReadbackFirmware",
    "firmwares.ModifiedFirmware",
    "firmwares.OtherFirmware",
    "firmwares.UnclassifiedFirmware",
    "firmwares.FlashingTool",
    "pages.Page",
)


def invalidate_widgets(sender: type[Model], **kwargs: object) -> None:
    invalidate_widget_caches(pages_changed=sender._meta.label == "pages.Page")


for _label in WIDGET_SOURCE_MODELS:
    post_save.connect(
        invalidate_widgets, sender=_label, dispatch_uid=f"pages-widgets-save-{_label}"
    )
    post_delete.connect(
        invalidate_widgets,
        sender=_label,
        dispatch_uid=f"pages-widgets-delete-{_label}",
    )
