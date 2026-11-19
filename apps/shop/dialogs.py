"""Shop-owned staff dialog forms (console action dialogs).

Core only provides the generic dialog shell; what each dialog asks lives here.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.admin_base import ConfirmForm, ReasonForm
from apps.core.admin_forms import BaseDialogForm, ConsoleTextInputWidget

__all__ = [
    "ArchivePackageForm",
    "CancelOrderForm",
    "CancelPackageForm",
    "DuplicatePackageForm",
    "MarkPaidForm",
    "RefundOrderForm",
    "ToggleActiveForm",
]


class MarkPaidForm(BaseDialogForm):
    reference = forms.CharField(
        label=_("Payment reference"),
        required=False,
        max_length=200,
        help_text=_("Receipt or transfer reference, kept on the order."),
        widget=ConsoleTextInputWidget,
    )


class RefundOrderForm(ReasonForm):
    """Why a paid order is refunded; recorded on the order."""


class CancelOrderForm(ConfirmForm):
    """Confirms cancelling a pending order."""


class CancelPackageForm(ConfirmForm):
    """Confirms cancelling a user's package."""


class DuplicatePackageForm(ConfirmForm):
    """Confirms copying a package as a hidden, inactive draft."""


class ArchivePackageForm(ConfirmForm):
    """Confirms retiring a package and cancelling its pending orders."""


class ToggleActiveForm(ConfirmForm):
    """Confirms activating or deactivating a package."""
