"""Device-owned dialogs: freeing a browser slot (core only provides the dialog shell).

User dialogs (``DialogView``): ask staff, pay credits, use a rewarded-ad reset. Staff
forms (Unfold action dialogs) are at the bottom.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextareaWidget

from apps.core.dialogs import DialogError, DialogForm, DialogView
from apps.devices import slots
from apps.devices.credits import CreditsUnavailable, InsufficientCreditsError
from apps.users.services.rate_limit import allow_action

__all__ = [
    "AskStaffDialog",
    "DeclineSlotRequestForm",
    "PayCreditsDialog",
    "ResetSlotsForm",
    "UseRewardedResetDialog",
]

_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SLOT_ACTIONS_PER_HOUR = 10


def _new_key() -> str:
    return uuid.uuid4().hex


class RequestKeyForm(DialogForm):
    """Carries the idempotency key minted when the dialog opened."""

    request_key = forms.CharField(widget=forms.HiddenInput)

    def clean_request_key(self) -> str:
        key = str(self.cleaned_data["request_key"])
        if not _KEY_PATTERN.fullmatch(key):
            raise forms.ValidationError(_("This form expired. Reload the page."))
        return key


class AskStaffForm(DialogForm):
    message = forms.CharField(
        label=_("Message for staff (optional)"),
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class SlotDialog(DialogView):
    """Shared behaviour: only at the limit, rate limited, errors shown in the dialog."""

    success_url_name = "devices:my_devices"
    size = "md"

    def slot_options(self) -> slots.SlotOptions:
        return slots.slot_options(self.request.user)

    def can_submit(self, options: slots.SlotOptions) -> bool:
        raise NotImplementedError("Slot dialogs must implement can_submit().")

    def perform(self, form: DialogForm) -> None:
        raise NotImplementedError("Slot dialogs must implement perform().")

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        options = self.slot_options()
        context["options"] = options
        if not self.can_submit(options):
            # Without a form action the shell renders only the close button.
            context["dialog_action"] = ""
            context["dialog_hx"] = False
        return context

    def check_rate_limit(self) -> None:
        key = f"devices:slot-dialog:{self.request.user.pk}"
        if not allow_action(
            key, max_attempts=SLOT_ACTIONS_PER_HOUR, window_seconds=3600
        ):
            raise DialogError(str(_("Too many attempts. Try again in a little while.")))

    def require_at_limit(self) -> None:
        options = self.slot_options()
        if not options.at_limit:
            raise DialogError(
                str(_("You still have free slots, so nothing needs to be reset."))
            )


class AskStaffDialog(SlotDialog):
    form_class = AskStaffForm
    template_name = "devices/dialogs/ask_staff.html"
    dialog_id = "ask-staff-slot-dialog"
    dialog_title = _("Ask staff to free a slot")
    dialog_message = _(
        "Staff review the request and can release your approved browsers. You will "
        "get a notification when it is answered."
    )
    confirm_label = _("Send request")
    success_message = _("Request sent. Staff will answer in your notifications.")

    def perform(self, form: DialogForm) -> None:
        self.check_rate_limit()
        self.require_at_limit()
        slots.request_staff_reset(
            self.request.user, str(form.cleaned_data.get("message", ""))
        )

    def can_submit(self, options: slots.SlotOptions) -> bool:
        return not options.request_open


class PayCreditsDialog(SlotDialog):
    form_class = RequestKeyForm
    template_name = "devices/dialogs/pay_credits.html"
    dialog_id = "pay-slot-reset-dialog"
    dialog_title = _("Free your browser slots")
    confirm_label = _("Pay and reset")
    success_message = _(
        "Slots reset. Approve the browsers you want to keep from this page."
    )

    def get_initial(self) -> dict[str, Any]:
        return {"request_key": _new_key()}

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        price = context["options"].credits_price
        if price is not None:
            context["confirm_label"] = _("Pay %(price)d credits and reset") % {
                "price": price
            }
        return context

    def can_submit(self, options: slots.SlotOptions) -> bool:
        return options.can_pay and options.missing_credits == 0

    def perform(self, form: DialogForm) -> None:
        self.check_rate_limit()
        self.require_at_limit()
        try:
            slots.reset_slots_with_credits(
                self.request.user, key=str(form.cleaned_data["request_key"])
            )
        except InsufficientCreditsError as error:
            raise DialogError(
                str(
                    _("You need %(missing)d more credits.")
                    % {"missing": error.needed - error.available}
                )
            ) from error
        except CreditsUnavailable as error:
            raise DialogError(
                str(_("Credits cannot be used for this right now."))
            ) from error
        except slots.SlotResetError as error:
            raise DialogError(error.message) from error


class UseRewardedResetDialog(SlotDialog):
    form_class = RequestKeyForm
    template_name = "devices/dialogs/use_rewarded_reset.html"
    dialog_id = "use-rewarded-reset-dialog"
    dialog_title = _("Use your rewarded-ad reset")
    dialog_message = _(
        "This spends the reset you earned by watching a rewarded ad and releases "
        "your approved browsers."
    )
    confirm_label = _("Reset my slots")
    success_message = _(
        "Slots reset. Approve the browsers you want to keep from this page."
    )

    def get_initial(self) -> dict[str, Any]:
        return {"request_key": _new_key()}

    def can_submit(self, options: slots.SlotOptions) -> bool:
        return options.allowances > 0 and not options.cap_reached

    def perform(self, form: DialogForm) -> None:
        self.check_rate_limit()
        self.require_at_limit()
        try:
            slots.reset_slots_with_allowance(
                self.request.user, key=str(form.cleaned_data["request_key"])
            )
        except slots.SlotResetError as error:
            raise DialogError(error.message) from error


# --- staff (Unfold action dialogs) --------------------------------------------


class ResetSlotsForm(BaseDialogForm):
    """Why staff release an account's approved browsers; recorded in the audit."""

    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


class DeclineSlotRequestForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Answer for the user"),
        max_length=255,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )
