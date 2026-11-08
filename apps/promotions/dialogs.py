"""The app-owned claim confirmation dialog (core owns only the shell)."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.core.dialogs import DialogError, DialogForm, DialogView

from .audience import audience_label
from .grants import ClaimError, claim, claim_earned
from .models import Promotion, PromotionKind
from .offers import OfferState, offer_state
from .views import visible_promotion


class ClaimDialog(DialogView):
    """Confirm taking an offer; the service re-checks everything on POST."""

    template_name = "promotions/dialogs/claim.html"
    dialog_id = "claim-offer-dialog"
    dialog_title = _("Take this offer")
    confirm_label = _("Claim offer")
    size = "md"
    promotion: Promotion

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.promotion = visible_promotion(request.user, kwargs["slug"])

    def current_state(self) -> OfferState:
        return offer_state(self.request.user, self.promotion)

    def get_context_data(self, form: DialogForm) -> dict[str, Any]:
        context = super().get_context_data(form)
        context.update(
            {
                "dialog_title": self.promotion.name,
                "promotion": self.promotion,
                "state": self.current_state(),
                "effect": self.promotion.effect_summary(),
                "terms": [
                    line for line in self.promotion.terms.splitlines() if line.strip()
                ],
                "audience_text": audience_label(self.promotion),
            }
        )
        return context

    def perform(self, form: DialogForm) -> None:
        user = self.request.user
        try:
            if self.promotion.kind == PromotionKind.EARN_BY_CONTRIBUTING:
                claim_earned(user, self.promotion)
            else:
                claim(user, self.promotion)
        except ClaimError as error:
            raise DialogError(error.message) from error

    def get_success_url(self) -> str | None:
        return self.promotion.get_absolute_url()

    def get_success_message(self) -> str:
        return f"{self.promotion.name}: {self.promotion.effect_summary()}"
