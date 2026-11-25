"""Member submission and status pages for catalog update proposals."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.users.decorators import email_verification_required

from .catalog_update_forms import CatalogUpdateValuesForm
from .catalog_update_proposals import TARGET_CONFIG, submit, target_config
from .models import CatalogUpdateProposal


@login_required
@email_verification_required
@require_http_methods(["GET", "POST"])
def propose_catalog_update(
    request: HttpRequest, target_kind: str, target_id: int
) -> HttpResponse:
    """Submit a proposed change to an existing firmware catalog item."""
    try:
        model_class, _target_field, _ = target_config(target_kind)
    except ValidationError as error:
        raise Http404("Unsupported catalog item.") from error
    target = get_object_or_404(model_class, pk=target_id)
    form = CatalogUpdateValuesForm(
        request.POST or None,
        target_kind=target_kind,
        changed_only=True,
        initial_values={
            name: getattr(target, name) for name in TARGET_CONFIG[target_kind][2]
        },
    )
    if request.method == "POST" and form.is_valid():
        try:
            result = submit(
                request.user,
                target_kind=target_kind,
                target_id=target_id,
                values=form.proposed_values(),
            )
        except PermissionDenied as error:
            form.add_error(None, str(error))
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            messages.success(
                request,
                "Your catalog update has been submitted for staff review."
                if result.created
                else "Your matching update proposal is already awaiting review.",
            )
            return redirect("firmwares:my_catalog_update_proposals")
    return render(
        request,
        "firmwares/catalog_update_form.html",
        {
            "target": target,
            "target_kind": target_kind,
            "form": form,
            "status_url": reverse("firmwares:my_catalog_update_proposals"),
        },
    )


@login_required
@email_verification_required
@require_http_methods(["GET"])
def my_catalog_update_proposals(request: HttpRequest) -> HttpResponse:
    """List catalog update proposals submitted by the signed-in member."""
    user = request.user
    if not user.is_authenticated:
        raise PermissionDenied("A verified account is required.")
    proposals = (
        CatalogUpdateProposal.objects.filter(submitted_by=user)
        .select_related(
            "brand",
            "chipset_vendor",
            "chipset",
            "model",
            "flashing_tool",
            "guide_template",
            "reviewed_by",
        )
        .order_by("-created_at")
    )
    return render(
        request,
        "firmwares/my_catalog_update_proposals.html",
        {"proposals": proposals},
    )
