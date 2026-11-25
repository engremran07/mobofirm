"""Staff review queue for member-submitted firmware catalog corrections."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.admin import register
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html

from apps.core.admin_base import ModelAdmin, display
from apps.core.admin_pages import render_admin_page

from .catalog_update_forms import CatalogUpdateReviewForm
from .catalog_update_proposals import TARGET_CONFIG, review
from .models import CatalogUpdateProposal


@register(CatalogUpdateProposal)
class CatalogUpdateProposalAdmin(ModelAdmin):
    list_display = (
        "id",
        "target_kind",
        "target_label",
        "submitted_by",
        "status",
        "created_at",
        "review_link",
    )
    list_filter = ("target_kind", "status", "created_at")
    search_fields = ("submitted_by__username",)
    list_select_related = (
        "submitted_by",
        "reviewed_by",
        "brand",
        "chipset_vendor",
        "chipset",
        "model",
        "flashing_tool",
        "guide_template",
    )

    @display(description="Catalog item")
    def target_label(self, proposal: CatalogUpdateProposal) -> str:
        for field in (
            "brand",
            "chipset_vendor",
            "chipset",
            "model",
            "flashing_tool",
            "guide_template",
        ):
            target = getattr(proposal, field)
            if target is not None:
                return str(target)
        return "—"

    @display(description="Review")
    def review_link(self, proposal: CatalogUpdateProposal) -> Any:
        if proposal.status != CatalogUpdateProposal.Status.PENDING:
            return "—"
        url = reverse(
            "admin:firmwares_catalogupdateproposal_review",
            args=(proposal.pk,),
        )
        return format_html('<a href="{}">Review proposal</a>', url)

    def get_urls(self) -> list[URLPattern]:
        review_url = path(
            "review/<int:proposal_id>/",
            self.admin_site.admin_view(self.review_proposal),
            name="firmwares_catalogupdateproposal_review",
        )
        return [review_url, *super().get_urls()]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: CatalogUpdateProposal | None = None
    ) -> bool:
        return False

    def has_view_permission(
        self, request: HttpRequest, obj: CatalogUpdateProposal | None = None
    ) -> bool:
        return bool(
            request.user.is_active
            and request.user.is_staff
            and any(
                request.user.has_perm(f"firmwares.change_{model.__name__.lower()}")
                for model, _field, _allowed in TARGET_CONFIG.values()
            )
        )

    def has_change_permission(
        self, request: HttpRequest, obj: CatalogUpdateProposal | None = None
    ) -> bool:
        return False

    def review_proposal(self, request: HttpRequest, proposal_id: int) -> HttpResponse:
        proposal = get_object_or_404(
            CatalogUpdateProposal.objects.select_related(
                "brand",
                "chipset_vendor",
                "chipset",
                "model",
                "flashing_tool",
                "guide_template",
                "submitted_by",
            ),
            pk=proposal_id,
        )
        target_field = TARGET_CONFIG[proposal.target_kind][1]
        target_model = TARGET_CONFIG[proposal.target_kind][0]
        permission = f"firmwares.change_{target_model.__name__.lower()}"
        if not request.user.has_perm(permission):
            raise PermissionDenied(
                "You do not have permission to review this catalog item."
            )
        target = getattr(proposal, target_field)
        form = CatalogUpdateReviewForm(
            request.POST or None,
            target_kind=proposal.target_kind,
            initial_values=proposal.proposed_values,
            initial={
                "decision": "approve",
                **proposal.proposed_values,
            },
        )
        if request.method == "POST" and form.is_valid():
            try:
                review(
                    proposal.pk,
                    request.user,
                    decision=form.cleaned_data["decision"],
                    reason=form.cleaned_data["reason"],
                    values=form.proposed_values(),
                )
            except ValidationError as error:
                for message in error.messages:
                    form.add_error(None, message)
            else:
                messages.success(request, "Catalog update proposal reviewed.")
                return redirect("admin:firmwares_catalogupdateproposal_changelist")

        return render_admin_page(
            request,
            "firmwares/admin/catalog_update_review.html",
            title="Review catalog update",
            subtitle=f"{proposal.get_target_kind_display()}: {target}",
            breadcrumbs=(
                (
                    "Firmware catalog proposals",
                    reverse("admin:firmwares_catalogupdateproposal_changelist"),
                ),
                ("Review", None),
            ),
            context={
                "proposal": proposal,
                "target": target,
                "form": form,
            },
        )
