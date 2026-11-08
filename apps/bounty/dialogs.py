"""Staff-free dialogs owned by bounty (core provides only the dialog shell)."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.bounty import services
from apps.bounty.models import Bounty
from apps.core.dialogs import DialogError, DialogForm, DialogView


class CancelBountyForm(DialogForm):
    reason_required = False


class CancelBountyDialog(DialogView):
    form_class = CancelBountyForm
    dialog_id = "cancel-bounty-dialog"
    dialog_title = _("Cancel this bounty?")
    dialog_message = _("Your escrowed credits are returned in full.")
    confirm_label = _("Cancel bounty")
    cancel_label = _("Keep it")
    danger = True
    success_message = _("Bounty cancelled and credits returned.")

    bounty: Bounty

    def dispatch(self, request: Any, *args: Any, **kwargs: Any):
        self.bounty = get_object_or_404(Bounty, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def has_permission(self, request: Any) -> bool:
        return request.user.pk == self.bounty.creator_id or request.user.has_perm(
            "bounty.change_bounty"
        )

    def perform(self, form: DialogForm) -> None:
        try:
            services.cancel(self.bounty, actor=self.request.user, reason=form.reason)
        except services.BountyError as error:
            raise DialogError(str(error)) from error

    def get_success_url(self) -> str | None:
        return reverse("bounty:detail", kwargs={"pk": self.bounty.pk})
