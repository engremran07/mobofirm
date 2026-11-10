"""Access type dialogs on the firmware page: staff change it, uploaders ask for it.

Both are app-owned ``DialogView`` dialogs (see ``docs/05-frontend/dialogs.md``).
Staff holding the file's ``change`` permission move an approved file between free,
featured and paid; the uploader of an approved file who is an approved seller files a
request that staff decide in the admin queue. Normal users cannot change anything.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from django import forms
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.dialogs import DialogError, DialogForm, DialogView

from . import access_changes, access_requests
from .access_change_models import AccessChangeSource
from .access_changes import AccessChange, AccessChangeError
from .catalog import get_firmware_model
from .models import AccessTier, BaseFirmware


class AccessTypeForm(DialogForm):
    """Access type and price fields shared by the staff and request dialogs."""

    reason_required = True
    access_tier = forms.ChoiceField(choices=AccessTier.choices, label=_("Access type"))
    price = forms.DecimalField(
        label=_("Price (USD)"),
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        help_text=_("Only for paid files. The credits price follows the rate."),
    )

    def clean(self) -> dict[str, Any]:
        """A paid file needs a price; the other types must not carry one."""
        cleaned = super().clean() or {}
        tier, price = cleaned.get("access_tier"), cleaned.get("price")
        if tier == AccessTier.PAID and not price:
            self.add_error("price", _("A paid file needs a price."))
        if tier in (AccessTier.FREE, AccessTier.FEATURED) and price:
            self.add_error("price", _("Only paid files have a price."))
        return cleaned


class FirmwareAccessDialog(DialogView):
    """Common lookup of the typed firmware row named in the URL."""

    size = "md"

    @cached_property
    def firmware(self) -> BaseFirmware:
        """The approved firmware record of this dialog."""
        model_class = get_firmware_model(str(self.kwargs["firmware_type"]))
        if model_class is None:
            raise Http404
        return get_object_or_404(
            model_class.objects.select_related("uploader", "brand", "model"),
            pk=self.kwargs["firmware_id"],
            is_verified=True,
        )

    def get_success_url(self) -> str | None:
        """Back to the firmware page (or a safe ``next``)."""
        fallback = reverse(
            "firmwares:firmware_detail",
            kwargs={
                "firmware_type": self.kwargs["firmware_type"],
                "firmware_id": self.kwargs["firmware_id"],
            },
        )
        return super().get_success_url() or fallback

    def get_initial(self) -> dict[str, Any]:
        """Start from the file's current access type and price."""
        return {
            "access_tier": self.firmware.access_tier,
            "price": self.firmware.approved_price,
        }

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        """Add the file and its current access type for the dialog body."""
        context = super().get_context_data(form)
        context.update(
            firmware=self.firmware,
            dialog_template=self.template_name,
        )
        return context

    def perform(self, form: DialogForm) -> None:
        """Abstract: concrete dialogs do the work for a valid ``form``."""
        raise NotImplementedError("Access dialogs must implement perform().")


class ChangeAccessDialog(FirmwareAccessDialog):
    """Staff: change the access type (and price) of an approved file."""

    template_name = "firmwares/dialogs/access_change.html"
    form_class = AccessTypeForm
    dialog_id = "firmware-access-dialog"
    dialog_title = _("Change the access type")
    dialog_message = _(
        "Free, featured (members with a package) or paid. People who already "
        "downloaded the file keep their access."
    )
    confirm_label = _("Change access type")
    success_message = _("The access type was changed.")

    def has_permission(self, request: HttpRequest) -> bool:
        """Staff holding the ``change`` permission of this firmware table."""
        user = request.user
        model_class = get_firmware_model(str(self.kwargs["firmware_type"]))
        if model_class is None:
            return False
        permission = (
            f"{model_class._meta.app_label}.change_{model_class._meta.model_name}"
        )
        return bool(user.is_staff and user.has_perm(permission))

    def perform(self, form: DialogForm) -> None:
        """Apply the change through the audited service."""
        try:
            access_changes.apply_access_change(
                self.firmware,
                AccessChange(
                    to_tier=str(form.cleaned_data["access_tier"]),
                    price=form.cleaned_data.get("price") or None,
                    actor=self.request.user,
                    source=AccessChangeSource.DIALOG,
                    reason=form.reason,
                ),
            )
        except AccessChangeError as error:
            raise DialogError(str(error)) from error


class RequestAccessDialog(FirmwareAccessDialog):
    """Uploader: ask staff to change the access type (and propose a price)."""

    template_name = "firmwares/dialogs/access_request.html"
    form_class = AccessTypeForm
    dialog_id = "firmware-access-request-dialog"
    dialog_title = _("Request an access type change")
    dialog_message = _(
        "Staff review every request. For a paid file, propose a price; staff may "
        "approve another one."
    )
    confirm_label = _("Send request")
    success_message = _("Request sent. Staff will review it.")

    def has_permission(self, request: HttpRequest) -> bool:
        """The uploader of this approved file."""
        return access_requests.can_request(request.user, self.firmware)

    def perform(self, form: DialogForm) -> None:
        """File the request through the service."""
        try:
            access_requests.submit_request(
                self.firmware,
                self.request.user,
                to_tier=str(form.cleaned_data["access_tier"]),
                proposed_price=form.cleaned_data.get("price") or None,
                reason=form.reason,
            )
        except AccessChangeError as error:
            raise DialogError(str(error)) from error
