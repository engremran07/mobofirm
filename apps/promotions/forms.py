"""Staff dialog forms for promotion lifecycle actions (Unfold action dialogs)."""

from __future__ import annotations

from apps.core.admin_base import ConfirmForm

__all__ = [
    "ActivateForm",
    "DuplicateForm",
    "EndForm",
    "ScheduleForm",
    "UnscheduleForm",
]


class ScheduleForm(ConfirmForm):
    """Confirms scheduling a validated draft."""


class ActivateForm(ConfirmForm):
    """Confirms starting a promotion immediately."""


class EndForm(ConfirmForm):
    """Confirms ending a promotion now."""


class UnscheduleForm(ConfirmForm):
    """Confirms returning a scheduled promotion to draft."""


class DuplicateForm(ConfirmForm):
    """Confirms copying a promotion as a new draft."""
